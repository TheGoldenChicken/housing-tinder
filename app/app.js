/* Housing Tinder — swipe through Danish homes. Vanilla JS PWA.
   Data: static JSON produced by the scraper (data/index.json + data/<cat>/<municipality>.json).
   Swipes: localStorage + optional Google Apps Script endpoint (sheets/Code.gs). */
"use strict";

const CATS = { sale: "For sale", rent: "For rent", andel: "Andel" };
const TYPE_LABEL = { house: "House", townhouse: "Townhouse", apartment: "Apartment", villa_apartment: "Villa apt.", farm: "Farm", room: "Room", other: "Other" };
const DEFAULT_TYPES = ["house", "townhouse", "apartment", "villa_apartment", "farm"];
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const fmt = new Intl.NumberFormat("da-DK");
const store = {
  get(k, d) { try { const v = localStorage.getItem("ht." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ht." + k, JSON.stringify(v)); } catch (e) { console.warn(e); } },
  del(k) { try { localStorage.removeItem("ht." + k); } catch {} },
};

const state = {
  cat: store.get("tab", "sale"),
  settings: Object.assign({ user: "", endpoint: "", sheetUrl: "", dataUrl: "./data", partner: "" }, store.get("settings", {})),
  swipes: store.get("swipes", {}),     // id -> {a: like|pass, t: ms, c: cat}
  liked: store.get("liked", {}),       // id -> listing (cache for the likes view)
  outbox: store.get("outbox", []),     // pending posts to the sheet
  matches: store.get("matches", {}),   // id -> ms when we learned both of us liked it
  partnerLiked: new Set(),             // ids the partner has liked (from the sheet)
  index: null,
  listings: {},                        // cat -> array (filtered by municipality files)
  loadedKey: {},                       // cat -> key of what is loaded
  queue: [],
  history: [],                         // for undo: [{id, listing, prev}]
  photoIdx: 0,
};

function defaultFilters(cat) {
  return { municipalities: [], zips: "", price_min: "", price_max: "", monthly_max: "", size_min: "", size_max: "", rooms_min: "", rooms_max: "",
    types: cat === "rent" ? DEFAULT_TYPES : DEFAULT_TYPES, year_min: "", year_max: "", year_unknown_ok: true,
    req_bathroom: false, req_balcony: false, req_elevator: false, req_pets: false, req_unlimited: false,
    floor_max: "", lot_min: "", energy_min: "", energy_unknown_ok: true, max_age_days: "", radius_km: "", center_lat: "", center_lon: "", sort: "newest" };
}
function getFilters(cat) { return Object.assign(defaultFilters(cat), store.get("filters." + cat, {})); }
function saveFilters(cat, f) { store.set("filters." + cat, f); }

/* ---------- toast ---------- */
let toastTimer;
function toast(msg, ms = 2200) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), ms);
}

/* ---------- data ---------- */
async function fetchJson(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}
async function loadIndex() {
  state.index = await fetchJson(`${state.settings.dataUrl}/index.json`);
  const i = state.index;
  $("#data-info").textContent = `Data from ${i.generated_at.replace("T", " ").slice(0, 16)} UTC · ${fmt.format(i.counts.sale)} for sale, ${fmt.format(i.counts.rent)} for rent, ${fmt.format(i.counts.andel)} andel. Sources: ` +
    Object.entries(i.sources).map(([k, v]) => `${k} ${v.ok ? "✓" : "✗"} (${v.count})`).join(", ");
}
async function loadCategory(cat) {
  const f = getFilters(cat);
  const files = state.index.files[cat] || {};
  const munis = f.municipalities.length && f.municipalities.length <= 40 ? f.municipalities.map(String) : null;
  const key = (munis ? munis.join(",") : "all") + "@" + state.index.generated_at;
  if (state.loadedKey[cat] === key) return;
  setStatus("Loading listings…");
  let items = [];
  if (munis) {
    const parts = await Promise.all(munis.filter((m) => files[m]).map((m) => fetchJson(`${state.settings.dataUrl}/${files[m]}`).catch(() => [])));
    items = parts.flat();
  } else {
    items = await fetchJson(`${state.settings.dataUrl}/${cat}/all.json`).catch(() => []);
  }
  state.listings[cat] = items;
  state.loadedKey[cat] = key;
}

/* ---------- filtering ---------- */
function parseZips(s) {
  const set = new Set();
  (s || "").split(/[,\s;]+/).filter(Boolean).forEach((tok) => {
    const m = tok.match(/^(\d{3,4})\s*-\s*(\d{3,4})$/);
    if (m) { for (let z = +m[1]; z <= +m[2]; z++) set.add(z); } else if (/^\d{3,4}$/.test(tok)) set.add(+tok);
  });
  return set;
}
function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371, dLat = (lat2 - lat1) * Math.PI / 180, dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}
function energyRank(label) { if (!label) return null; const c = label[0].toUpperCase(); return "ABCDEFG".indexOf(c) >= 0 ? "ABCDEFG".indexOf(c) : null; }
const num = (v) => (v === "" || v === null || v === undefined ? null : +v);

function buildMatcher(cat, f) {
  const zips = parseZips(f.zips);
  const munis = new Set(f.municipalities.map(Number));
  const types = new Set(f.types);
  const pmin = num(f.price_min), pmax = num(f.price_max), mmax = num(f.monthly_max);
  const smin = num(f.size_min), smax = num(f.size_max), rmin = num(f.rooms_min), rmax = num(f.rooms_max);
  const ymin = num(f.year_min), ymax = num(f.year_max), fmax = num(f.floor_max), lmin = num(f.lot_min);
  const emin = energyRank(f.energy_min), age = num(f.max_age_days), radius = num(f.radius_km);
  const clat = num(f.center_lat), clon = num(f.center_lon);
  const cutoff = age ? Date.now() - age * 86400000 : null;
  return (l) => {
    if (state.swipes[l.id]) return false;
    if (munis.size && !munis.has(l.municipality_code || 0)) return false;
    if (zips.size && !zips.has(l.zip_code)) return false;
    if (types.size && !types.has(l.property_type || "other")) return false;
    if (pmin !== null && (l.price ?? 0) < pmin) return false;
    if (pmax !== null && (l.price ?? 0) > pmax) return false;
    if (mmax !== null && (l.monthly_cost ?? 0) > mmax) return false;
    if (smin !== null && (l.size_m2 ?? 0) < smin) return false;
    if (smax !== null && l.size_m2 && l.size_m2 > smax) return false;
    if (rmin !== null && (l.rooms ?? 0) < rmin) return false;
    if (rmax !== null && l.rooms && l.rooms > rmax) return false;
    if (ymin !== null || ymax !== null) {
      if (l.year_built) { if (ymin !== null && l.year_built < ymin) return false; if (ymax !== null && l.year_built > ymax) return false; }
      else if (!f.year_unknown_ok) return false;
    }
    if (f.req_bathroom && !((l.bathrooms ?? 0) > 0 || l.has_bathroom === true)) return false;
    if (f.req_balcony && l.balcony !== true && l.terrace !== true) return false;
    if (f.req_elevator && l.elevator !== true) return false;
    if (f.req_pets && l.pets_allowed !== true) return false;
    if (f.req_unlimited && l.rental_period && !/unlimited|ubegr/i.test(l.rental_period)) return false;
    if (fmax !== null && l.floor != null && l.floor > fmax) return false;
    if (lmin !== null && (l.lot_m2 ?? 0) < lmin) return false;
    if (emin !== null) { const r = energyRank(l.energy_label); if (r === null) { if (!f.energy_unknown_ok) return false; } else if (r > emin) return false; }
    if (cutoff && l.first_seen && Date.parse(l.first_seen) < cutoff) return false;
    if (radius && clat !== null && clon !== null) { if (l.lat == null || haversine(clat, clon, l.lat, l.lon) > radius) return false; }
    return true;
  };
}
function sortQueue(items, sort) {
  const by = {
    newest: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || "") || (b.listed_date || "").localeCompare(a.listed_date || ""),
    price_asc: (a, b) => (a.price ?? 1e12) - (b.price ?? 1e12),
    price_desc: (a, b) => (b.price ?? 0) - (a.price ?? 0),
    size_desc: (a, b) => (b.size_m2 ?? 0) - (a.size_m2 ?? 0),
  }[sort];
  if (by) return items.sort(by);
  // daily-seeded shuffle so the order is stable within a day
  let seed = +new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const rnd = () => (seed = (seed * 9301 + 49297) % 233280) / 233280;
  return items.map((x) => [rnd(), x]).sort((a, b) => a[0] - b[0]).map((x) => x[1]);
}
function rebuildQueue() {
  const cat = state.cat, f = getFilters(cat);
  const all = state.listings[cat] || [];
  const match = buildMatcher(cat, f);
  state.queue = sortQueue(all.filter(match), f.sort);
  state.total = all.length;
  renderDeck();
}

/* ---------- rendering ---------- */
function setStatus(txt) { $("#status").textContent = txt; }
function money(v, cat) { if (v == null) return "—"; return `${fmt.format(v)} kr.${cat === "rent" ? "/md" : ""}`; }
function cardHtml(l, cat) {
  const isNew = l.first_seen && (Date.now() - Date.parse(l.first_seen)) < 3 * 86400000;
  const chips = [];
  if (l.size_m2) chips.push(`${l.size_m2} m²`);
  if (l.rooms) chips.push(`${l.rooms} rooms`);
  if (l.floor != null) chips.push(l.floor === 0 ? "ground floor" : `${l.floor}. floor`);
  if (l.year_built) chips.push(`built ${l.year_built}`);
  if (l.bathrooms != null) chips.push(`${l.bathrooms} bath`); else if (l.has_bathroom === true) chips.push("own bath");
  if (l.lot_m2) chips.push(`lot ${fmt.format(l.lot_m2)} m²`);
  if (l.energy_label) chips.push(`energy ${l.energy_label}`);
  chips.push(TYPE_LABEL[l.property_type] || "Other");
  if (l.balcony) chips.push("balcony"); if (l.elevator) chips.push("elevator"); if (l.terrace) chips.push("terrace");
  if (l.pets_allowed === true) chips.push("pets ok"); if (l.furnished) chips.push("furnished");
  if (l.available_from) chips.push(`from ${l.available_from}`); if (l.rental_period) chips.push(l.rental_period);
  const sub = [];
  if (cat === "rent") { if (l.deposit) sub.push(`deposit ${fmt.format(l.deposit)}`); if (l.monthly_cost) sub.push(`+ ${fmt.format(l.monthly_cost)} aconto`); }
  else { if (l.monthly_cost) sub.push(`${fmt.format(l.monthly_cost)} kr./md ${cat === "andel" ? "boligafgift" : "ejerudgift"}`); if (l.size_m2 && l.price) sub.push(`${fmt.format(Math.round(l.price / l.size_m2))} kr./m²`); }
  const links = Object.entries((l.extra && l.extra.urls) || { [l.source]: l.url });
  const photos = l.images || [];
  return `
    <div class="photos" style="background-image:url('${photos[0] || ""}')">
      ${photos.length ? `<div class="dots">${photos.map((_, i) => `<i class="${i === 0 ? "on" : ""}"></i>`).join("")}</div>` : `<div class="nophoto">No photo</div>`}
      <div class="zone l"></div><div class="zone r"></div>
      <div class="stamp like">LIKE</div><div class="stamp nope">NOPE</div><div class="stamp super">SUPER</div>
    </div>
    <div class="info">
      <div class="price">${money(l.price, cat)}${isNew ? '<span class="badge">NEW</span>' : ""}${sub.length ? `<span class="sub">${sub.join(" · ")}</span>` : ""}</div>
      <div class="addr">${esc(l.address || "")}${l.address ? ", " : ""}${l.zip_code || ""} ${esc(l.city || "")}${l.municipality ? ` <span class="muted">· ${esc(l.municipality)}</span>` : ""}</div>
      <div class="chips">${chips.map((c) => `<span>${esc(c)}</span>`).join("")}</div>
      ${l.title ? `<div class="title">${esc(l.title)}</div>` : ""}
      ${l.description ? `<div class="desc">${esc(l.description)}</div>` : ""}
      <div class="links">${links.map(([s, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener">Open on ${esc(s)} ↗</a>`).join("")}</div>
      <div class="meta">${l.realtor ? esc(l.realtor) + " · " : ""}listed ${l.listed_date || "?"} · first seen ${l.first_seen || "?"}</div>
    </div>`;
}
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

function renderDeck() {
  const deck = $("#deck"); deck.innerHTML = "";
  const cat = state.cat;
  const n = state.queue.length;
  setStatus(n ? `${fmt.format(n)} match${n === 1 ? "" : "es"} left · ${fmt.format(state.total || 0)} ${CATS[cat].toLowerCase()} loaded` : "");
  $("#empty").classList.toggle("hidden", n > 0);
  $("#controls").classList.toggle("hidden", n === 0);
  if (!n) {
    $("#empty-text").textContent = state.total ? "You have swiped through everything matching your filters. Loosen them or come back after the next scrape." : "No listings loaded for this category yet.";
    return;
  }
  // bottom-most first so the top card is last in DOM
  state.queue.slice(0, 3).reverse().forEach((l, i, arr) => {
    const el = document.createElement("div");
    el.className = "card " + (i === arr.length - 1 ? "" : i === arr.length - 2 ? "behind" : "behind2");
    el.dataset.id = l.id;
    el.innerHTML = cardHtml(l, cat);
    deck.appendChild(el);
  });
  state.photoIdx = 0;
  attachSwipe($(".card:last-child", deck));
}

/* ---------- swipe mechanics ---------- */
function attachSwipe(card) {
  if (!card) return;
  let startX = 0, startY = 0, dx = 0, dy = 0, dragging = false, moved = false, t0 = 0, downTarget = null;
  const photos = $(".photos", card), desc = $(".desc", card);
  const like = $(".stamp.like", card), nope = $(".stamp.nope", card), sup = $(".stamp.super", card);
  const isUp = () => dy < -110 && Math.abs(dx) < Math.abs(dy) * 0.6;
  // Pointer capture makes later events target the card, so remember what was tapped on pointerdown.
  const onDown = (e) => { if (e.button && e.button !== 0) return; dragging = true; moved = false; downTarget = e.target; startX = e.clientX; startY = e.clientY; t0 = Date.now(); card.setPointerCapture(e.pointerId); card.classList.add("dragging"); };
  const onMove = (e) => {
    if (!dragging) return;
    dx = e.clientX - startX; dy = e.clientY - startY;
    if (Math.abs(dx) > 6 || Math.abs(dy) > 6) moved = true;
    if (!moved) return;
    if (Math.abs(dy) > Math.abs(dx) * 1.5 && e.target.closest(".info")) { return; } // let info scroll vertically
    const upward = dy < 0 && Math.abs(dx) < Math.abs(dy) * 0.6;
    card.style.transform = `translate(${dx}px, ${upward ? dy * 0.6 : dy * 0.3}px) rotate(${dx / 18}deg)`;
    like.style.opacity = upward ? 0 : Math.min(1, Math.max(0, dx / 90)); nope.style.opacity = upward ? 0 : Math.min(1, Math.max(0, -dx / 90));
    sup.style.opacity = upward ? Math.min(1, Math.max(0, -dy / 110)) : 0;
  };
  const onUp = (e) => {
    if (!dragging) return; dragging = false; card.classList.remove("dragging");
    const vx = dx / Math.max(1, Date.now() - t0);
    if (moved && isUp()) { flyOut(card, "superlike"); return; }
    if (moved && (Math.abs(dx) > 110 || Math.abs(vx) > 0.6)) { flyOut(card, dx > 0 ? "like" : "pass"); return; }
    card.style.transform = ""; like.style.opacity = 0; nope.style.opacity = 0; sup.style.opacity = 0;
    if (!moved && downTarget && e.type !== "pointercancel") {
      const zone = downTarget.closest(".zone");
      if (zone) stepPhoto(card, zone.classList.contains("r") ? 1 : -1);
      else if (downTarget.closest(".desc")) desc.classList.toggle("open");
    }
    downTarget = null;
    dx = dy = 0;
  };
  card.addEventListener("pointerdown", onDown);
  card.addEventListener("pointermove", onMove);
  card.addEventListener("pointerup", onUp);
  card.addEventListener("pointercancel", onUp);
  photos.addEventListener("click", (e) => e.preventDefault());
}
function stepPhoto(card, dir) {
  const l = state.queue[0]; if (!l || !l.images || l.images.length < 2) return;
  state.photoIdx = (state.photoIdx + dir + l.images.length) % l.images.length;
  $(".photos", card).style.backgroundImage = `url('${l.images[state.photoIdx]}')`;
  $$(".dots i", card).forEach((d, i) => d.classList.toggle("on", i === state.photoIdx));
}
function flyOut(card, action) {
  const dir = action === "like" ? 1 : -1;
  card.style.transform = action === "superlike"
    ? `translate(0, ${-(window.innerHeight + 200)}px) scale(.9)`
    : `translate(${dir * (window.innerWidth + 200)}px, -40px) rotate(${dir * 30}deg)`;
  card.style.opacity = "0";
  setTimeout(() => commit(action), 200);
}
function commit(action) {
  const l = state.queue.shift(); if (!l) return;
  state.history.push({ id: l.id, listing: l });
  state.swipes[l.id] = { a: action, t: Date.now(), c: state.cat };
  store.set("swipes", state.swipes);
  if (action === "like" || action === "superlike") { state.liked[l.id] = { ...l, superlike: action === "superlike" }; store.set("liked", state.liked); }
  enqueue({ action, listing: slim(l) });
  if ((action === "like" || action === "superlike") && state.partnerLiked.has(l.id)) recordMatches([l.id]);
  updateLikesCount();
  renderDeck();
}
function undo() {
  const h = state.history.pop(); if (!h) { toast("Nothing to undo"); return; }
  const was = state.swipes[h.id];
  delete state.swipes[h.id]; store.set("swipes", state.swipes);
  if (was && (was.a === "like" || was.a === "superlike")) { delete state.liked[h.id]; delete state.matches[h.id]; store.set("liked", state.liked); store.set("matches", state.matches); enqueue({ action: "unlike", listing: slim(h.listing) }); }
  else enqueue({ action: "undo", listing: slim(h.listing) });
  state.queue.unshift(h.listing);
  updateLikesCount();
  renderDeck();
}
function slim(l) { const { description, ...rest } = l; return { ...rest, description: (description || "").slice(0, 300) }; }
function updateLikesCount() { const n = Object.keys(state.liked).length; $("#likes-count").textContent = n ? String(n) : ""; }

/* ---------- sheet sync (Google Apps Script) ---------- */
function enqueue(item) {
  if (!state.settings.endpoint) return;
  state.outbox.push({ ...item, t: Date.now() }); store.set("outbox", state.outbox);
  flushOutbox();
}
let flushing = false;
async function flushOutbox() {
  const { endpoint, user } = state.settings;
  if (flushing || !endpoint || !user || !state.outbox.length) return;
  flushing = true;
  const batch = state.outbox.slice(0, 50);
  try {
    const r = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "text/plain;charset=utf-8" }, body: JSON.stringify({ user, partner: state.settings.partner || "", action: "batch", swipes: batch }), redirect: "follow" });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || "sheet error");
    state.outbox = state.outbox.slice(batch.length); store.set("outbox", state.outbox);
    $("#sync-info").textContent = `Last sync ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    console.warn("sync failed", e); $("#sync-info").textContent = `Sync failed: ${e.message} (${state.outbox.length} pending)`;
  } finally { flushing = false; if (state.outbox.length) setTimeout(flushOutbox, 15000); }
}
async function pullRemote() {
  const { endpoint, user } = state.settings;
  if (!endpoint || !user) return;
  try {
    const j = await fetchJson(`${endpoint}?user=${encodeURIComponent(user)}&partner=${encodeURIComponent(state.settings.partner || "")}&_=${Date.now()}`);
    state.partnerLiked = new Set(j.partner_liked || []);
    let added = 0;
    (j.liked || []).forEach((id) => { if (!state.swipes[id]) { state.swipes[id] = { a: "like", t: 0, remote: true }; added++; } });
    (j.superliked || []).forEach((id) => { if (!state.swipes[id] || state.swipes[id].a === "like") { state.swipes[id] = { a: "superlike", t: state.swipes[id]?.t || 0, remote: true }; added++; } });
    (j.passed || []).forEach((id) => { if (!state.swipes[id]) { state.swipes[id] = { a: "pass", t: 0, remote: true }; added++; } });
    // liked on another device: cache listing for the likes view when we can find it
    Object.values(state.listings).flat().forEach((l) => {
      const sw = state.swipes[l.id];
      if (sw && (sw.a === "like" || sw.a === "superlike") && (!state.liked[l.id] || !!state.liked[l.id].superlike !== (sw.a === "superlike"))) state.liked[l.id] = { ...l, superlike: sw.a === "superlike" };
    });
    if (added) { store.set("swipes", state.swipes); store.set("liked", state.liked); }
    updateLikesCount();
    const fresh = (j.matches || []).filter((id) => !state.matches[id]);
    if (fresh.length) recordMatches(fresh);
    $("#sync-info").textContent = `Synced ${new Date().toLocaleTimeString()} · ${(j.liked || []).length} likes on sheet`;
  } catch (e) { console.warn("pull failed", e); $("#sync-info").textContent = `Could not read sheet: ${e.message}`; }
}

/* ---------- matches ---------- */
function findListing(id) {
  return state.liked[id] || Object.values(state.listings).flat().find((l) => l.id === id) || null;
}
function recordMatches(ids) {
  const now = Date.now();
  ids.forEach((id) => { state.matches[id] = now; });
  store.set("matches", state.matches);
  showMatch(ids);
}
function showMatch(ids) {
  const partner = state.settings.partner || "your partner";
  const items = ids.map(findListing).filter(Boolean);
  $("#match-text").textContent = ids.length === 1
    ? `You and ${partner} both liked this one.`
    : `You and ${partner} both liked ${ids.length} homes.`;
  const list = $("#match-list"); list.innerHTML = "";
  items.forEach((l) => {
    const el = document.createElement("div"); el.className = "like-item";
    el.innerHTML = `${l.images?.[0] ? `<img src="${esc(l.images[0])}" loading="lazy">` : '<div class="noimg"></div>'}
      <div class="t"><b>${money(l.price, l.category)}</b><div class="a">${esc(l.address || "")}, ${l.zip_code || ""} ${esc(l.city || "")}</div>
      <div class="muted">${l.size_m2 ? l.size_m2 + " m² · " : ""}<a href="${esc(l.url)}" target="_blank" rel="noopener">open ↗</a></div></div>`;
    list.appendChild(el);
  });
  if (!items.length) list.innerHTML = `<p class="muted">Details are in the sheet's Matches tab.</p>`;
  $("#dlg-match").showModal();
  if (navigator.vibrate) navigator.vibrate([60, 40, 60]);
}

/* ---------- dialogs ---------- */
function openFilters() {
  const cat = state.cat, f = getFilters(cat), form = $("#form-filters");
  $("#filters-title").textContent = `Filters · ${CATS[cat]}`;
  $("#price-legend").textContent = cat === "rent" ? "Monthly rent (kr.)" : "Price (kr.)";
  $("#fs-monthly").classList.toggle("hidden", cat === "rent");
  $$("[data-cat]", form).forEach((el) => el.classList.toggle("hidden", el.dataset.cat !== cat));
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") el.checked = el.name === "types" ? f.types.includes(el.value) : !!f[el.name];
    else el.value = f[el.name] ?? "";
  }
  // municipalities
  const list = $("#muni-list"); list.innerHTML = "";
  const sel = new Set(f.municipalities.map(Number));
  (state.index?.municipalities || []).filter((m) => m[cat] > 0 || sel.has(m.code)).sort((a, b) => a.name.localeCompare(b.name, "da")).forEach((m) => {
    const lab = document.createElement("label");
    lab.innerHTML = `<span style="color:inherit"><input type="checkbox" value="${m.code}" ${sel.has(m.code) ? "checked" : ""}> ${esc(m.name)}</span><span>${m[cat]}</span>`;
    list.appendChild(lab);
  });
  updateMuniCount();
  $("#muni-search").value = "";
  $("#dlg-filters").showModal();
}
function updateMuniCount() { const n = $$("#muni-list input:checked").length; $("#muni-selected").textContent = n ? `${n} selected` : "all of Denmark"; }
function readFilters() {
  const cat = state.cat, form = $("#form-filters"), f = defaultFilters(cat);
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.name === "types") continue;
    if (el.type === "checkbox") f[el.name] = el.checked; else f[el.name] = el.value.trim();
  }
  f.types = $$("input[name=types]:checked", form).map((e) => e.value);
  f.municipalities = $$("#muni-list input:checked").map((e) => +e.value);
  return f;
}
async function applyFilters() {
  const f = readFilters(); saveFilters(state.cat, f);
  await loadCategory(state.cat).catch((e) => toast("Load failed: " + e.message));
  rebuildQueue();
}
function openLikes() {
  const list = $("#likes-list"); list.innerHTML = "";
  const isMatch = (l) => (state.matches[l.id] ? 1 : 0);
  const items = Object.values(state.liked).sort((a, b) => isMatch(b) - isMatch(a) || (b.superlike ? 1 : 0) - (a.superlike ? 1 : 0) || (state.swipes[b.id]?.t || 0) - (state.swipes[a.id]?.t || 0));
  $("#likes-sheet-link").innerHTML = state.settings.sheetUrl ? `Full details for review: <a href="${esc(state.settings.sheetUrl)}" target="_blank" rel="noopener">open the Google Sheet ↗</a>` : (state.settings.endpoint ? "Likes are also stored in your Google Sheet." : "Likes are stored on this device only (set a sheet endpoint in settings to sync).");
  if (!items.length) list.innerHTML = '<p class="muted">No likes yet.</p>';
  items.forEach((l) => {
    const el = document.createElement("div"); el.className = "like-item";
    el.innerHTML = `${l.images?.[0] ? `<img src="${esc(l.images[0])}" loading="lazy">` : '<div class="noimg"></div>'}
      <div class="t"><b>${money(l.price, l.category)}${state.matches[l.id] ? '<span class="badge match">MATCH</span>' : ""}${l.superlike ? '<span class="badge super">★ SUPER</span>' : ""}</b><div class="a">${esc(l.address || "")}, ${l.zip_code || ""} ${esc(l.city || "")}</div>
      <div class="muted">${l.size_m2 ? l.size_m2 + " m² · " : ""}${TYPE_LABEL[l.property_type] || ""} · <a href="${esc(l.url)}" target="_blank" rel="noopener">open ↗</a></div></div>
      <button title="Remove like" data-id="${l.id}">✕</button>`;
    $("button", el).onclick = () => {
      delete state.liked[l.id]; delete state.matches[l.id]; state.swipes[l.id] = { a: "pass", t: Date.now(), c: l.category };
      store.set("liked", state.liked); store.set("matches", state.matches); store.set("swipes", state.swipes); enqueue({ action: "unlike", listing: slim(l) });
      updateLikesCount(); el.remove();
    };
    list.appendChild(el);
  });
  $("#dlg-likes").showModal();
}
function exportCsv() {
  const cols = ["match", "superlike", "id", "category", "property_type", "address", "zip_code", "city", "municipality", "price", "monthly_cost", "deposit", "size_m2", "lot_m2", "rooms", "floor", "bathrooms", "year_built", "energy_label", "available_from", "listed_date", "first_seen", "url", "title"];
  const rows = [cols.join(";")].concat(Object.values(state.liked).map((l) => cols.map((c) => `"${String(c === "superlike" ? (l.superlike ? "yes" : "no") : c === "match" ? (state.matches[l.id] ? "yes" : "no") : (l[c] ?? "")).replace(/"/g, '""')}"`).join(";")));
  const blob = new Blob(["﻿" + rows.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "housing-likes.csv"; a.click();
}
function openSettings() {
  const form = $("#form-settings");
  for (const el of form.elements) if (el.name) el.value = state.settings[el.name] ?? "";
  $("#dlg-settings").showModal();
}
function saveSettings() {
  const form = $("#form-settings");
  for (const el of form.elements) if (el.name) state.settings[el.name] = el.value.trim();
  if (!state.settings.dataUrl) state.settings.dataUrl = "./data";
  state.settings.dataUrl = state.settings.dataUrl.replace(/\/$/, "");
  if (state.settings.partner && !state.settings.endpoint) toast("Partner matching needs a sheet endpoint", 4000);
  store.set("settings", state.settings);
}

/* ---------- boot ---------- */
async function switchTab(cat) {
  state.cat = cat; store.set("tab", cat);
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.cat === cat));
  state.history = [];
  try { await loadCategory(cat); } catch (e) { toast("Load failed: " + e.message); }
  rebuildQueue();
}
async function reloadAll() {
  state.loadedKey = {};
  try { await loadIndex(); await switchTab(state.cat); toast("Data reloaded"); } catch (e) { setStatus("Could not load data: " + e.message); }
}
function bind() {
  $$("#tabs button").forEach((b) => (b.onclick = () => switchTab(b.dataset.cat)));
  $("#btn-filters").onclick = openFilters; $("#btn-empty-filters").onclick = openFilters; $("#btn-empty-likes").onclick = openLikes;
  $("#btn-likes").onclick = openLikes; $("#btn-likes-close").onclick = () => $("#dlg-likes").close();
  $("#btn-settings").onclick = openSettings;
  $("#btn-like").onclick = () => { const c = $("#deck .card:last-child"); if (c) flyOut(c, "like"); };
  $("#btn-pass").onclick = () => { const c = $("#deck .card:last-child"); if (c) flyOut(c, "pass"); };
  $("#btn-super").onclick = () => { const c = $("#deck .card:last-child"); if (c) flyOut(c, "superlike"); };
  $("#btn-undo").onclick = undo;
  $("#form-filters").addEventListener("submit", applyFilters);
  $("#form-settings").addEventListener("submit", async () => { saveSettings(); await reloadAll(); pullRemote(); flushOutbox(); });
  $("#muni-search").addEventListener("input", (e) => { const q = e.target.value.toLowerCase(); $$("#muni-list label").forEach((l) => l.classList.toggle("hidden", !l.textContent.toLowerCase().includes(q))); });
  $("#muni-list").addEventListener("change", updateMuniCount);
  $("#muni-clear").onclick = () => { $$("#muni-list input").forEach((i) => (i.checked = false)); updateMuniCount(); };
  $("#btn-reset-filters").onclick = () => { store.del("filters." + state.cat); $("#dlg-filters").close(); applyFilters(); };
  $("#btn-geoloc").onclick = () => navigator.geolocation?.getCurrentPosition((p) => { $("[name=center_lat]").value = p.coords.latitude.toFixed(5); $("[name=center_lon]").value = p.coords.longitude.toFixed(5); }, () => toast("Location unavailable"));
  $("#btn-export").onclick = exportCsv;
  $("#btn-match-close").onclick = () => $("#dlg-match").close();
  $("#btn-sync").onclick = async () => { saveSettings(); await pullRemote(); await flushOutbox(); rebuildQueue(); toast("Synced"); };
  $("#btn-reload").onclick = () => { saveSettings(); reloadAll(); };
  $("#btn-reset-swipes").onclick = () => { if (confirm("Forget all swipes stored on this device? (The sheet is not changed.)")) { state.swipes = {}; state.liked = {}; store.set("swipes", {}); store.set("liked", {}); updateLikesCount(); rebuildQueue(); $("#dlg-settings").close(); } };
  document.addEventListener("keydown", (e) => {
    if ($$("dialog[open]").length) return;
    if (e.key === "ArrowRight") $("#btn-like").click(); else if (e.key === "ArrowLeft") $("#btn-pass").click(); else if (e.key === "ArrowUp") $("#btn-super").click(); else if (e.key === "Backspace") undo();
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { pullRemote().then(rebuildQueue); flushOutbox(); } });
}

(async function boot() {
  bind();
  updateLikesCount();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  if (!state.settings.user) { toast("Set your name in settings (⋯) to sync swipes", 4000); }
  try { await loadIndex(); } catch (e) { setStatus("Could not load data: " + e.message + " (run the scraper first)"); return; }
  await switchTab(state.cat);
  await pullRemote(); rebuildQueue(); flushOutbox();
})();
