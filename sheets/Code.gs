/**
 * Housing Tinder — Google Apps Script backend.
 *
 * Stores swipes in the Google Sheet this script is bound to:
 *   - "Likes"   : one row per right-swipe with all listing details + link (review these later)
 *   - "Swipes"  : every swipe (like / pass / unlike), used to sync state between devices
 *   - "Matches" : homes liked by both users of a pair (user + partner from the app settings)
 *
 * Setup (once):
 *   1. Create a Google Sheet. Extensions → Apps Script. Replace the code with this file. Save.
 *   2. Deploy → New deployment → type "Web app". Execute as: Me. Who has access: Anyone. Deploy.
 *   3. Copy the Web app URL (ends in /exec) into the app's Settings → "Sheet endpoint".
 *
 * API:
 *   GET  ?user=karl[&partner=anna]  → {"liked":[ids], "superliked":[ids], "passed":[ids],
 *                                       "partner_liked":[ids], "matches":[ids]}   (last two with partner)
 *   POST body (text/plain JSON), optional "partner" on any post:
 *        {"user":"karl","partner":"anna","action":"like"|"superlike"|"pass"|"unlike","listing":{...}} → {"ok":true}
 *        {"user":"karl","partner":"anna","action":"batch","swipes":[{action, listing}, ...]} → {"ok":true,"n":N}
 */

var LIKE_COLUMNS = [
  "liked_at", "user", "id", "category", "property_type", "address", "zip_code", "city", "municipality",
  "price", "monthly_cost", "deposit", "size_m2", "lot_m2", "rooms", "floor", "bathrooms", "year_built",
  "energy_label", "balcony", "elevator", "pets_allowed", "available_from", "listed_date", "first_seen",
  "url", "other_urls", "title", "description", "realtor", "image", "lat", "lon", "sources", "super"
];
var SWIPE_COLUMNS = ["at", "user", "action", "id", "category", "address", "url"];
var MATCH_COLUMNS = ["matched_at", "users", "id", "category", "property_type", "address", "zip_code", "city", "municipality",
  "price", "size_m2", "rooms", "url", "title", "image", "superliked_by"];

function doGet(e) {
  var user = ((e && e.parameter && e.parameter.user) || "").trim();
  var partner = ((e && e.parameter && e.parameter.partner) || "").trim();
  var state = userState_(user);
  var liked = [], superliked = [], passed = [];
  for (var id in state) {
    if (state[id] === "like") liked.push(id);
    else if (state[id] === "superlike") superliked.push(id);
    else if (state[id] === "pass") passed.push(id);
  }
  var out = { liked: liked, superliked: superliked, passed: passed };
  if (partner && partner !== user) {
    var pstate = userState_(partner);
    var partnerLiked = [];
    for (var pid in pstate) if (isLike_(pstate[pid])) partnerLiked.push(pid);
    out.partner_liked = partnerLiked;
    var both = partnerLiked.filter(function (id) { return isLike_(state[id]); });
    out.matches = ensureMatches_(user, partner, both, null);
  }
  return json_(out);
}

/** id -> last action for one user, from the Swipes tab. */
function userState_(user) {
  var rows = sheet_("Swipes", SWIPE_COLUMNS).getDataRange().getValues();
  var state = {};
  for (var i = 1; i < rows.length; i++) {
    if (String(rows[i][1]) !== user) continue;
    state[String(rows[i][3])] = String(rows[i][2]);
  }
  return state;
}

function isLike_(action) { return action === "like" || action === "superlike"; }

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var body = JSON.parse(e.postData.contents);
    var user = String(body.user || "").trim();
    if (!user) return json_({ ok: false, error: "missing user" });
    var partner = String(body.partner || "").trim();
    var swipes = body.action === "batch" ? body.swipes : [{ action: body.action, listing: body.listing }];
    var n = 0;
    swipes.forEach(function (s) { record_(user, s.action, s.listing || {}); n++; });
    if (partner && partner !== user) {
      var pstate = userState_(partner);
      swipes.forEach(function (s) {
        var id = String((s.listing || {}).id || "");
        if (isLike_(s.action) && isLike_(pstate[id])) ensureMatches_(user, partner, [id], s.listing);
      });
    }
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
  // a match needs both likes, so drop it when either side unlikes
  var matches = sheet_("Matches", MATCH_COLUMNS);
  var mrows = matches.getDataRange().getValues();
  for (var j = mrows.length - 1; j >= 1; j--) {
    if (String(mrows[j][2]) === String(id) && String(mrows[j][1]).split(" + ").indexOf(user) >= 0) matches.deleteRow(j + 1);
  }
}

/**
 * Make sure every id in `ids` has a row in Matches for this pair. Details come from the
 * posted listing when given, otherwise from either user's row in Likes. Returns the ids.
 */
function ensureMatches_(user, partner, ids, listing) {
  if (!ids.length) return ids;
  var pair = [user, partner].sort().join(" + ");
  var matches = sheet_("Matches", MATCH_COLUMNS);
  var rows = matches.getDataRange().getValues();
  var have = {};
  for (var i = 1; i < rows.length; i++) have[String(rows[i][1]) + "|" + String(rows[i][2])] = true;
  var missing = ids.filter(function (id) { return !have[pair + "|" + id]; });
  if (!missing.length) return ids;
  var likesRows = sheet_("Likes", LIKE_COLUMNS).getDataRange().getValues();
  var header = likesRows[0] || LIKE_COLUMNS;
  var col = function (name) { return header.indexOf(name); };
  missing.forEach(function (id) {
    var l = listing && String(listing.id) === id ? listing : null;
    var superBy = [];
    for (var r = 1; r < likesRows.length; r++) {
      var row = likesRows[r];
      if (String(row[col("id")]) !== id) continue;
      var who = String(row[col("user")]);
      if (who !== user && who !== partner) continue;
      if (col("super") >= 0 && row[col("super")] === true) superBy.push(who);
      if (!l) {
        l = {};
        LIKE_COLUMNS.forEach(function (c) { if (col(c) >= 0) l[c] = row[col(c)]; });
        l.images = l.image ? [l.image] : [];
      }
    }
    l = l || { id: id };
    matches.appendRow(MATCH_COLUMNS.map(function (c) {
      if (c === "matched_at") return new Date();
      if (c === "users") return pair;
      if (c === "id") return id;
      if (c === "image") return (l.images && l.images[0]) || l.image || "";
      if (c === "superliked_by") return superBy.join(", ");
      var v = l[c];
      return v === undefined || v === null ? "" : v;
    }));
  });
  return ids;
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
