// ── Map setup: Esri satellite basemap + labels overlay ───────────────────────
const map = L.map("map", { maxZoom: 19, rotate: true, touchRotate: false }).setView([28.6139, 77.2090], 10);

// Custom pane for labels so they sit above terrain polygons (overlayPane z=400)
// but below aircraft markers (markerPane z=600)
map.createPane("labelsPane");
map.getPane("labelsPane").style.zIndex = 450;
map.getPane("labelsPane").style.pointerEvents = "none";

// Layer 1: high-res satellite imagery (land + ocean real-world appearance)
L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    attribution:   "&copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    maxZoom:       19,
    maxNativeZoom: 19,
  }
).addTo(map);

// Layer 2: transparent place-name labels — cities, countries, water body names
// (Arabian Sea, Bay of Bengal…), roads, admin borders at every zoom level.
// Rendered above terrain polygons but below aircraft markers.
L.tileLayer(
  "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
  {
    attribution:   "",
    maxZoom:       19,
    maxNativeZoom: 19,
    pane:          "labelsPane",
  }
).addTo(map);

const terrainLayer = L.layerGroup().addTo(map);
const lzLayer      = L.layerGroup().addTo(map);
const trafficLayer = L.layerGroup().addTo(map);

// ── State ─────────────────────────────────────────────────────────────────────
let sessionId        = null;
let ws               = null;
let currentLat       = 28.6139;
let currentLon       = 77.2090;
let currentAlt       = 5000;
let currentSpd       = 100;
let currentHdg       = 90;
let selectedAcId     = null;
let selectedAcMarker = null;
let aircraftFeed     = [];
let latestData            = null;
let _voiceEnabled   = false;
let _lastVoiceTxt   = "";
let _lastVoiceTs    = 0;
let _nightMode      = false;
let _glideRangeNm   = 0;
let _oskyAuthHeader       = null;   // set from /api/opensky-creds on init
let _cachedOskyLocal      = [];     // local OpenSky results, persist between fetchAircraft() calls
// Risk state — position posted to /api/live-state for risk grid calculation.
// Follows selected aircraft; falls back to home base when nothing is selected.
let _riskLat    = 28.6139;
let _riskLon    = 77.2090;
// Previous position of the selected aircraft — used to detect movement for live-state re-POST.
let _selPrevLat = null;
let _selPrevLon = null;
// When true, fetchAircraft() will auto-select the nearest aircraft after the feed loads.
let _autoSelectFirst = false;

// ── Dead reckoning state ──────────────────────────────────────────────────────
// Between 6-second ADS-B polls, the aircraft position and altitude are projected
// forward using the last known speed, heading, and vertical speed.
// Altitude DR is safety-critical: a 2000 ft/min descent loses 200 ft per 6-second
// poll cycle — enough to shift risk from MODERATE to CRITICAL undetected.
let _drBaseLat    = null;   // lat at last real poll
let _drBaseLon    = null;   // lon at last real poll
let _drBaseAlt    = null;   // altitude_ft at last real poll
let _drSpeedKts   = 0;      // ground speed at last real poll
let _drHeadingDeg = 0;      // heading at last real poll
let _drVsFpm      = 0;      // vertical speed (ft/min) at last real poll
let _drBaseTime   = 0;      // Date.now() at last real poll
let _drInterval   = null;   // 1-second DR timer handle
let _drPostCtr    = 0;      // counter: post to backend every 2nd tick

// ── Overpass-fetched context — nearest airport and road ───────────────────────
let _nearestAirport  = null;   // { name, icao, dist_km }
let _nearestRoad     = null;   // { type, dist_km }
let _airportFetchKey = "";     // quantised cache key to avoid redundant calls
let _roadFetchKey    = "";

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(v) {
  return String(v ?? "").replace(
    /[&<>"']/g,
    (s) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[s]
  );
}

function riskToColor(r) {
  if (r < 0.25) return "#2cb64f";
  if (r < 0.45) return "#7dc840";
  if (r < 0.60) return "#d8d62b";
  if (r < 0.75) return "#ff9c00";
  return "#ba2627";
}

function haversine(lat1, lon1, lat2, lon2) {
  const R    = 6371;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a    =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
    Math.sin(dLon / 2) ** 2;
  return Math.round(6371 * 2 * Math.asin(Math.sqrt(a)) * 10) / 10;
}

// Compass bearing (0–360°) from point 1 to point 2.
function bearingTo(lat1, lon1, lat2, lon2) {
  const φ1 = lat1 * Math.PI / 180;
  const φ2 = lat2 * Math.PI / 180;
  const Δλ = (lon2 - lon1) * Math.PI / 180;
  const y   = Math.sin(Δλ) * Math.cos(φ2);
  const x   = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

// ── Overpass helpers — nearest airport & road (cached, fired in background) ───
const _OVERPASS = "https://overpass-api.de/api/interpreter";

function _overpassCacheKey(lat, lon, res = 0.05) {
  return `${(Math.round(lat / res) * res).toFixed(2)},${(Math.round(lon / res) * res).toFixed(2)}`;
}

async function fetchNearestAirport(lat, lon) {
  const key = _overpassCacheKey(lat, lon);
  if (key === _airportFetchKey) return;
  _airportFetchKey = key;
  const q = `[out:json][timeout:10];nwr[aeroway=aerodrome](around:200000,${lat},${lon});out center;`;
  try {
    const resp = await fetch(_OVERPASS, {
      method: "POST", body: q, headers: { "Content-Type": "text/plain" },
    });
    // Stale-result guard: discard if user switched location while we were fetching
    if (_airportFetchKey !== key) return;
    const els  = (await resp.json()).elements || [];
    let best   = null, bestDist = Infinity;
    for (const el of els) {
      const eLat = el.lat ?? el.center?.lat;
      const eLon = el.lon ?? el.center?.lon;
      if (eLat == null || eLon == null) continue;
      const d = haversine(lat, lon, eLat, eLon);
      if (d < bestDist) { bestDist = d; best = { ...el, lat: eLat, lon: eLon }; }
    }
    _nearestAirport = best
      ? { name:     best.tags?.name || "Unknown",
          icao:     best.tags?.icao || best.tags?.iata || "",
          dist_km:  Math.round(bestDist * 10) / 10,
          lat:      best.lat,   // stored for real-time distance + bearing updates
          lon:      best.lon }
      : null;
  } catch (_) { _nearestAirport = null; }
}

async function fetchNearestRoad(lat, lon) {
  const key = _overpassCacheKey(lat, lon);
  if (key === _roadFetchKey) return;
  _roadFetchKey = key;
  const q = `[out:json][timeout:8];way[highway~"^(motorway|trunk|primary|secondary)$"](around:30000,${lat},${lon});out center;`;
  try {
    const resp = await fetch(_OVERPASS, {
      method: "POST", body: q, headers: { "Content-Type": "text/plain" },
    });
    // Stale-result guard: discard if user switched location while we were fetching
    if (_roadFetchKey !== key) return;
    const els  = (await resp.json()).elements || [];
    let best   = null, bestDist = Infinity;
    for (const el of els) {
      const cLat = el.center?.lat ?? el.lat;
      const cLon = el.center?.lon ?? el.lon;
      if (cLat == null || cLon == null) continue;
      const d = haversine(lat, lon, cLat, cLon);
      if (d < bestDist) { bestDist = d; best = el; }
    }
    _nearestRoad = best
      ? { type: best.tags?.highway || "road", dist_km: Math.round(bestDist * 10) / 10 }
      : null;
  } catch (_) { _nearestRoad = null; }
}

// ── Risk grid (computed client-side from backend risk score) ─────────────────
function computeGrid(lat, lon, baseRisk, gridSize = 9, cellDeg = 0.004) {
  const cells = [];
  const half  = gridSize / 2;
  const seed  = (Math.abs(lat * 100) % 997) + (Math.abs(lon * 100) % 997);

  for (let row = -half; row < half; row++) {
    for (let col = -half; col < half; col++) {
      const clat = lat + row * cellDeg;
      const clon = lon + col * cellDeg;

      const variation =
        Math.sin(row * 0.9 + seed * 0.01) * 0.15 +
        Math.cos(col * 0.7 + seed * 0.01) * 0.15;
      const risk = Math.min(1, Math.max(0, baseRisk + variation));
      const r    = Math.round(risk * 100) / 100;

      cells.push({
        corners: [
          [clat, clon],
          [clat + cellDeg, clon],
          [clat + cellDeg, clon + cellDeg],
          [clat, clon + cellDeg],
        ],
        risk:          r,
        ground_safety: Math.round((1 - r) * 100) / 100,
        slope_deg:     Math.round(r * 18 * 10) / 10,
        obstacle:      r > 0.6 ? "Possible" : "None",
        color:         riskToColor(risk),
      });
    }
  }
  return cells;
}

// ── Aircraft markers ──────────────────────────────────────────────────────────
function trafficIcon(selected = false) {
  const color  = selected ? "#80ffdb" : "#60a5fa";
  const dot    = selected ? 18 : 14;
  const pad    = 8;                        // invisible padding for easier clicking
  const total  = dot + pad * 2;
  const border = selected ? "2.5px solid #062b2e" : "1.5px solid #0f172a";
  const glow   = selected
    ? "0 0 0 4px rgba(128,255,219,0.30), 0 2px 8px rgba(0,0,0,0.5)"
    : "0 0 0 3px rgba(255,255,255,0.15), 0 1px 4px rgba(0,0,0,0.4)";
  return L.divIcon({
    className: "",
    // Outer div = transparent hit-area; inner div = visible dot
    html: `<div style="width:${total}px;height:${total}px;display:flex;
      align-items:center;justify-content:center;cursor:pointer;">
      <div style="width:${dot}px;height:${dot}px;border-radius:999px;
        background:${color};border:${border};box-shadow:${glow};
        transition:transform 0.15s;"></div>
    </div>`,
    iconSize:   [total, total],
    iconAnchor: [total / 2, total / 2],
  });
}

// ── Map bearing helper + airplane icon ───────────────────────────────────────
// Architecture (confirmed from leaflet-rotate v0.2.8 source):
//
// • setBearing(X) applies CSS rotate(X rad) to the map PANE (CW in screen space),
//   so bearing (360−X)° faces screen-up.  Passing (360−H)%360 makes bearing H
//   face screen-up (the heading direction) — correct heading-up display.
//
// • Marker icons: Leaflet.Rotate _setPos calls setTransform(el, pos, undefined).
//   Because bearing=undefined is falsy, the override falls through to the
//   original Leaflet setTransform — zero CSS rotation is added to the icon.
//   The icon is only position-translated; SVG-up is always screen-up.
//
// • Therefore: in heading-up mode, screen-up = heading direction, and
//   airplaneIcon(0) (nose at SVG-up) makes the nose point in the heading
//   direction — no pre-rotation of vertices needed.
function setMapBearing(headingDeg) {
  // setBearing(X) rotates the pane CW by X → bearing (360-X) faces up.
  // Pass (360-H)%360 so bearing H (the heading direction) faces screen-up.
  if (map.setBearing) map.setBearing((360 - (headingDeg ?? 0)) % 360);
}

function airplaneIcon(headingDeg) {
  // Pre-rotate every SVG vertex by headingDeg CW around the viewBox centre
  // (12, 12).  No CSS transform is applied to the SVG, so the nose direction
  // is determined entirely by geometry — immune to any CSS-composition or
  // Leaflet.Rotate _setPos side-effects.
  //
  // CW rotation by angle H in SVG / screen y-down space around (cx, cy):
  //   x' = cx + (x-cx)*cos(H) - (y-cy)*sin(H)
  //   y' = cy + (x-cx)*sin(H) + (y-cy)*cos(H)
  // Verified: H=0 → identity; H=90 → nose (12,2) → (22,12) = screen-right ✓
  //           H=180 → nose → (12,22) = screen-bottom ✓
  //           H=150 → nose → (17,20.66) = SSE on north-up map ✓
  const rad = headingDeg * Math.PI / 180;
  const C = Math.cos(rad), S = Math.sin(rad);
  const cx = 12, cy = 12;
  function r(x, y) {
    const dx = x - cx, dy = y - cy;
    return `${(cx + dx * C - dy * S).toFixed(2)},${(cy + dx * S + dy * C).toFixed(2)}`;
  }

  // Original vertices (nose at y=2 = top → heading 0° = north)
  const fuse  = [r(12,2),  r(14.5,14), r(12,12.5), r(9.5,14) ].join(' ');
  const wings = [r(12,10), r(22,18),   r(21,20),   r(12,15),  r(3,20), r(2,18)].join(' ');
  const tail  = [r(12,15), r(13.5,22), r(12,21),   r(10.5,22)].join(' ');

  const sz = 44;
  return L.divIcon({
    className: "",
    html: `<div style="width:${sz}px;height:${sz}px;display:flex;align-items:center;justify-content:center;cursor:pointer;">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="30" height="30"
           style="overflow:visible;filter:drop-shadow(0 0 6px rgba(128,255,219,0.9));pointer-events:none;">
        <polygon points="${fuse}"  fill="#80ffdb" stroke="#062b2e" stroke-width="1.1"/>
        <polygon points="${wings}" fill="#80ffdb" stroke="#062b2e" stroke-width="1"/>
        <polygon points="${tail}"  fill="#80ffdb" stroke="#062b2e" stroke-width="1"/>
      </svg>
    </div>`,
    iconSize:   [sz, sz],
    iconAnchor: [sz / 2, sz / 2],
  });
}

function drawTraffic() {
  trafficLayer.clearLayers();
  // Remove old airplane marker — a fresh one will be created below
  if (selectedAcMarker) { map.removeLayer(selectedAcMarker); selectedAcMarker = null; }

  aircraftFeed.forEach((ac) => {
    const isSel = ac.id === selectedAcId;

    if (isSel) {
      // Selected aircraft: dedicated airplane icon placed directly on the map
      // (not in trafficLayer) so it always sits above all other markers and
      // persists independently of trafficLayer.clearLayers() on later ticks.
      selectedAcMarker = L.marker([ac.latitude, ac.longitude], {
        icon:            airplaneIcon(0),   // 0 = nose at SVG-up = screen-up = heading direction (map is already rotated)
        zIndexOffset:    3000,
        bubblingMouseEvents: false,
      }).addTo(map);
      selectedAcMarker.bindTooltip(
        `<strong>${esc(ac.callsign)}</strong><br>` +
        `Alt ${Math.round(ac.altitude_ft)} ft &nbsp;|&nbsp; Spd ${ac.speed_kts != null ? Math.round(ac.speed_kts) + ' kt' : '— kt'}<br>` +
        `Hdg ${Math.round(ac.heading_deg)}° &nbsp;|&nbsp; Dist ${ac.distance_km} km`,
        { direction: "top", offset: [0, -6], className: "terrain-tip" }
      );
      selectedAcMarker.on("mousedown", (e) => { L.DomEvent.stopPropagation(e); });
      selectedAcMarker.on("click", (e) => { L.DomEvent.stopPropagation(e); selectAircraft(ac); });
    } else {
      // Non-selected: small dot in the traffic layer
      const m = L.marker([ac.latitude, ac.longitude], {
        icon:            trafficIcon(false),
        zIndexOffset:    0,
        bubblingMouseEvents: false,
      }).addTo(trafficLayer);
      m.bindTooltip(
        `<strong>${esc(ac.callsign)}</strong><br>` +
        `Alt ${Math.round(ac.altitude_ft)} ft<br>` +
        `Spd ${ac.speed_kts != null ? Math.round(ac.speed_kts) + ' kt' : '—'}<br>` +
        `Hdg ${Math.round(ac.heading_deg)}°<br>` +
        `Dist ${ac.distance_km} km`,
        { direction: "top", offset: [0, -4] }
      );
      m.on("mousedown", (e) => { L.DomEvent.stopPropagation(e); });
      m.on("click", (e) => { L.DomEvent.stopPropagation(e); selectAircraft(ac); });
    }
  });
}

function drawTrafficList() {
  const q = (document.getElementById("trafficSearch")?.value || "").trim().toLowerCase();
  const filtered = aircraftFeed.filter((ac) => {
    const txt = `${ac.callsign} ${ac.id}`.toLowerCase();
    return !q || txt.includes(q);
  });

  const within20 = aircraftFeed.filter((a) => a.distance_km <= 20).length;
  const nearest  = aircraftFeed.length ? aircraftFeed[0].distance_km : null;

  document.getElementById("trafficSummary").innerHTML =
    `Feed aircraft: ${aircraftFeed.length}<br>` +
    `Within 20 km: ${within20}<br>` +
    `Nearest: ${nearest != null ? nearest + " km" : "--"}`;

  const list = document.getElementById("trafficList");
  list.innerHTML = "";

  if (!filtered.length) {
    list.innerHTML = `<div class="lz-item"><strong>No aircraft</strong><span>Try a different search or wait for feed update</span></div>`;
    return;
  }

  filtered.slice(0, 40).forEach((ac) => {
    const row = document.createElement("div");
    row.className = "lz-item" + (ac.id === selectedAcId ? " selected" : "");
    row.style.cursor = "pointer";
    row.innerHTML =
      `<strong>${esc(ac.callsign)}</strong>` +
      `<span>${Math.round(ac.altitude_ft)} ft • ${ac.speed_kts != null ? Math.round(ac.speed_kts) + ' kt' : '—'} • ${ac.distance_km} km</span>`;
    row.addEventListener("click", () => selectAircraft(ac));
    list.appendChild(row);
  });
}

// ── Select aircraft → update backend state ────────────────────────────────────
async function selectAircraft(ac) {
  selectedAcId = ac.id;
  // NOTE: currentLat/currentLon (home base) are NOT touched here.
  // They always stay at the searched/home location so the feed distances
  // remain relative to the searched location (e.g. Indore), not the aircraft.
  currentAlt  = ac.altitude_ft;
  currentSpd  = ac.speed_kts;
  currentHdg  = ac.heading_deg;

  // Track risk position separately — follows the selected aircraft
  _riskLat    = ac.latitude;
  _riskLon    = ac.longitude;
  _selPrevLat = ac.latitude;
  _selPrevLon = ac.longitude;

  // Start dead reckoning from this aircraft's current state
  _resetDR(ac);
  _startDR();

  await fetch(`/api/live-state/${sessionId}`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({
      latitude:     ac.latitude,
      longitude:    ac.longitude,
      altitude_ft:  ac.altitude_ft,
      speed_kts:    ac.speed_kts,
      heading_deg:  ac.heading_deg,
      callsign:     ac.callsign,
      icao24:       ac.icao24,
      forward_grid: true,   // nose-forward rotated grid
      aircraft_type: ac.aircraft_type || "UNKN",
      aircraft_reg:  ac.aircraft_reg  || "",
      vs_fpm:        ac.vs_fpm || 0,
    }),
  });

  // Clear stale home-position cells so the off-screen grid doesn't linger.
  // Fresh cells for the aircraft's position arrive on the next WebSocket tick (≤1.5 s).
  terrainLayer.clearLayers();
  lzLayer.clearLayers();

  // Rotate map to aircraft heading (heading-up mode) before flyTo so the
  // animation lands with the correct bearing already set.
  setMapBearing(ac.heading_deg);

  // Zoom 11: better cell + SVG readability when aircraft is selected
  map.flyTo([ac.latitude, ac.longitude], 11, { animate: true, duration: 0.8 });
  drawTraffic();
  drawTrafficList();

  // Fire background context fetches — result appears in Layers/Decision on next draw().
  // Reset cache keys so a newly selected aircraft always gets a fresh lookup even
  // if it happens to be at the same quantised position as the previous one.
  _airportFetchKey = "";
  _roadFetchKey    = "";
  fetchNearestAirport(ac.latitude, ac.longitude);
  fetchNearestRoad(ac.latitude, ac.longitude);
  // Do NOT redraw with latestData here — it holds home-position cells that are
  // now off-screen. The WebSocket delivers aircraft-position cells within 1.5 s.
}

// ── OpenSky client-side fetch ─────────────────────────────────────────────────
// Fetches all aircraft within ±3° of the current location from the user's
// browser (bypasses any datacenter IP block on OpenSky).
// Shows whatever is flying near the searched location — India, USA, Europe, etc.
let _oskyLastCall = 0;

async function fetchOpenSkyDirect() {
  const now = Date.now();
  // Registered users: 1 req / 10 s.  We use 15 s for safety.
  if (now - _oskyLastCall < 15_000) return [];
  _oskyLastCall = now;

  const d   = 3.0;   // ±3° ≈ 330 km for solid regional coverage
  const url = "https://opensky-network.org/api/states/all" +
    `?lamin=${(currentLat - d).toFixed(4)}&lomin=${(currentLon - d).toFixed(4)}` +
    `&lamax=${(currentLat + d).toFixed(4)}&lomax=${(currentLon + d).toFixed(4)}`;

  const headers = {};
  if (_oskyAuthHeader) headers["Authorization"] = _oskyAuthHeader;

  try {
    const res = await fetch(url, { headers, signal: AbortSignal.timeout(12_000) });
    if (!res.ok) return [];
    const data = await res.json();
    if (!data.states) return [];

    const results = [];
    for (const s of data.states) {
      // skip: no position or on ground
      if (s[6] == null || s[5] == null || s[8] !== false) continue;
      results.push({
        id:          s[0],
        callsign:    (s[1] || "").trim() || s[0],
        icao24:      s[0],
        latitude:    s[6],
        longitude:   s[5],
        altitude_ft: s[7] != null ? Math.round(s[7] * 3.28084) : 0,
        speed_kts:   s[9] != null ? Math.round(s[9] * 1.944)   : 0,
        heading_deg: s[10] || 0,
        distance_km: 0,   // recomputed from current location in fetchAircraft()
        source:      "opensky",
      });
    }
    return results;
  } catch (_) {
    return [];
  }
}

// ── Dead reckoning engine ─────────────────────────────────────────────────────

function _deadReckonLL(lat, lon, speedKts, headingDeg, seconds) {
  // Spherical dead reckoning: project (lat,lon) forward along heading by
  // distance = speed × time.  Returns [lat, lon] unchanged if speed is 0/null.
  if (!speedKts || speedKts <= 0) return [lat, lon];
  const dist_m = speedKts * 0.514444 * seconds;
  const R      = 6371000;
  const δ      = dist_m / R;
  const θ      = Math.PI / 180 * headingDeg;
  const φ1     = Math.PI / 180 * lat;
  const λ1     = Math.PI / 180 * lon;
  const φ2     = Math.asin(
    Math.sin(φ1) * Math.cos(δ) +
    Math.cos(φ1) * Math.sin(δ) * Math.cos(θ)
  );
  const λ2     = λ1 + Math.atan2(
    Math.sin(θ) * Math.sin(δ) * Math.cos(φ1),
    Math.cos(δ) - Math.sin(φ1) * Math.sin(φ2)
  );
  return [φ2 * 180 / Math.PI, λ2 * 180 / Math.PI];
}

function _resetDR(ac) {
  // Snapshot the aircraft state from the latest real ADS-B poll.
  _drBaseLat    = ac.latitude;
  _drBaseLon    = ac.longitude;
  _drBaseAlt    = ac.altitude_ft ?? 5000;
  _drSpeedKts   = ac.speed_kts  ?? 0;
  _drHeadingDeg = ac.heading_deg ?? 0;
  _drVsFpm      = ac.vs_fpm     ?? 0;
  _drBaseTime   = Date.now();
  _drPostCtr    = 0;
}

function _stopDR() {
  if (_drInterval) { clearInterval(_drInterval); _drInterval = null; }
}

function _startDR() {
  _stopDR();
  _drInterval = setInterval(() => {
    if (!selectedAcId || _drBaseLat == null) return;
    const elapsed = (Date.now() - _drBaseTime) / 1000;   // seconds since last real poll

    // Project position
    const [drLat, drLon] = _deadReckonLL(
      _drBaseLat, _drBaseLon, _drSpeedKts, _drHeadingDeg, elapsed
    );

    // Project altitude — clamp to ground
    const drAlt = Math.max(0, _drBaseAlt + (_drVsFpm / 60) * elapsed);

    // Smooth visual update (every 1 s) — no API call, just move the SVG icon
    if (selectedAcMarker) selectedAcMarker.setLatLng([drLat, drLon]);

    // Post dead-reckoned state to backend every 2 s so the WS risk engine
    // computes against the estimated current altitude, not 6-second-old data.
    // This is critical for descending aircraft: a 2000 ft/min descent loses
    // 200 ft per poll cycle — enough to change risk level without DR.
    _drPostCtr++;
    if (_drPostCtr % 2 === 0 && sessionId) {
      fetch(`/api/live-state/${sessionId}`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          latitude:    drLat,
          longitude:   drLon,
          altitude_ft: drAlt,
          vs_fpm:      _drVsFpm,
          speed_kts:   _drSpeedKts,
          heading_deg: _drHeadingDeg,
        }),
      }).catch(() => {});   // best-effort — silent on transient error
    }
  }, 1000);
}

// ── Fetch aircraft: backend proxy + browser-direct OpenSky ────────────────────
let _fetchAcInProgress = false;

async function fetchAircraft() {
  // Skip if a previous call is still in-flight to prevent overlapping requests
  if (_fetchAcInProgress) return;
  _fetchAcInProgress = true;
  try {
    // Run ADS-B backend call and browser-direct OpenSky in parallel
    const [backendRes, oskyFresh] = await Promise.all([
      fetch(`/api/aircraft?lat=${currentLat}&lon=${currentLon}&radius=200`).then((r) => r.json()),
      fetchOpenSkyDirect(),
    ]);

    // ── Normalise ADS-B feed ──
    // Use != null (not truthy) so aircraft at lat=0 or lon=0 (equator / prime
    // meridian — valid global coordinates) are never silently discarded.
    const adsbFeed = (backendRes.ac || [])
      .filter((ac) => ac.lat != null && ac.lon != null)
      .map((ac) => ({
        id:          ac.hex || "",
        callsign:    (ac.flight || "").trim() || ac.hex || "",
        icao24:      ac.hex || "",
        latitude:    ac.lat,
        longitude:   ac.lon,
        altitude_ft: ac.alt_baro || 0,
        speed_kts:   ac.gs ?? null,
        heading_deg: ac.track || 0,
        distance_km: haversine(currentLat, currentLon, ac.lat, ac.lon),
        source:      "adsb",
        aircraft_type: (ac.t  || ac.aircraft_type || "").toUpperCase().slice(0,4) || "UNKN",
        aircraft_reg:  (ac.r  || ac.aircraft_reg  || "").trim(),
        vs_fpm:        ac.vs_fpm || ac.baro_rate   || 0,
      }));

    // ── Update local OpenSky cache when fresh data arrived ──
    if (oskyFresh.length > 0) _cachedOskyLocal = oskyFresh;

    // ── Merge: cached OpenSky base, ADS-B overwrites duplicates (higher fidelity) ──
    const merged = new Map(_cachedOskyLocal.map((a) => [a.id, a]));
    for (const ac of adsbFeed) merged.set(ac.id, ac);

    // ── Recompute distance from HOME location (currentLat/currentLon is always home) ──
    // Filter to ≤250 km so distant aircraft don't distort "nearest" / "within 20 km" stats.
    aircraftFeed = [...merged.values()]
      .map((ac) => ({
        ...ac,
        distance_km: haversine(currentLat, currentLon, ac.latitude, ac.longitude),
      }))
      .filter((ac) => ac.distance_km <= 250)
      .sort((a, b) => a.distance_km - b.distance_km);

    // ── Track selected aircraft position → keep risk grid live as it moves ──
    // Compare against PREVIOUS aircraft position (not home), so drift > 0 only
    // when the aircraft itself has moved, not when the home base is far away.
    if (selectedAcId) {
      const selAc = aircraftFeed.find((a) => a.id === selectedAcId);
      if (selAc && _selPrevLat != null) {
        const drift = haversine(_selPrevLat, _selPrevLon, selAc.latitude, selAc.longitude);
        if (drift > 0.1) {
          _selPrevLat = selAc.latitude;
          _selPrevLon = selAc.longitude;
          _riskLat    = selAc.latitude;
          _riskLon    = selAc.longitude;
          // Reset DR base to the fresh real position so projections stay accurate
          _resetDR(selAc);
          currentAlt  = selAc.altitude_ft;
          currentSpd  = selAc.speed_kts;
          currentHdg  = selAc.heading_deg;
          // Post new position — backend immediately recalculates terrain risk grid
          fetch(`/api/live-state/${sessionId}`, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              latitude:     selAc.latitude,
              longitude:    selAc.longitude,
              altitude_ft:  selAc.altitude_ft,
              speed_kts:    selAc.speed_kts,
              heading_deg:  selAc.heading_deg,
              callsign:     selAc.callsign,
              icao24:       selAc.icao24,
              forward_grid: true,
              aircraft_type: selAc.aircraft_type || "UNKN",
              aircraft_reg:  selAc.aircraft_reg  || "",
              vs_fpm:        selAc.vs_fpm || 0,
            }),
          });
        }
      }
    }

    drawTraffic();
    drawTrafficList();

    // Auto-select the nearest aircraft after a location change
    if (_autoSelectFirst && aircraftFeed.length > 0) {
      _autoSelectFirst = false;
      selectAircraft(aircraftFeed[0]);
    }
  } catch (_) {
    // silent — keep previous aircraft state on transient errors
  } finally {
    _fetchAcInProgress = false;
  }
}

// ── Main draw — called on every WebSocket message ─────────────────────────────
function draw(data) {
  latestData = data;

  autoManageVoice();
  const vt = buildVoiceText(data);
  if (vt) speakAlert(vt);
  updateGlideOverlay(data);
  updateSigmetBanner(data);
  document.body.classList.toggle("critical-active", data.risk?.level==="CRITICAL");
  if (data.cells) {
    let idx = 0;
    terrainLayer.eachLayer(layer => {
      const cell = data.cells[idx++];
      if (cell && cell.reachable === false) {
        try { layer.setStyle({opacity:0.25, fillOpacity:0.1, dashArray:"5,4"}); } catch(e){}
      }
    });
  }

  // Derive overall failure risk from probabilistic engine
  const failRisk = data.probabilistic ? data.probabilistic.failure : 0.3;

  // Extract terrain and weather early — needed throughout draw()
  const terrain = data.terrain || {};
  const weather = data.weather || {};

  // ── Risk grid: use backend cells if present, else compute client-side ──────
  terrainLayer.clearLayers();
  lzLayer.clearLayers();

  const cells = (data.cells && data.cells.length)
    ? data.cells
    : computeGrid(currentLat, currentLon, failRisk);

  cells.forEach((cell) => {
    let tipHtml;
    if (cell.is_water) {
      const depth = cell.depth_m != null ? cell.depth_m : Math.abs(terrain.elevation_m || 0);
      tipHtml = `<strong>Water / Ocean</strong><br>Depth ~${depth} m<br>Risk ${cell.risk}<br><em>Ditching required</em>`;
    } else {
      const groundSafety = cell.ground_safety != null ? cell.ground_safety : Math.round((1 - cell.risk) * 100) / 100;
      const slope        = cell.slope_deg != null ? cell.slope_deg : (cell.slope != null ? cell.slope : "--");
      const obstacle     = cell.obstacle  != null ? cell.obstacle  : (cell.risk > 0.6 ? "Possible" : "None");
      tipHtml = `Risk ${cell.risk}<br>Ground ${groundSafety}<br>Slope ${slope}°<br>Obstacle ${obstacle}`;
    }

    L.polygon(cell.corners, {
      color:       cell.color,
      fillColor:   cell.color,
      fillOpacity: cell.is_water ? 0.32 : 0.28,
      opacity:     0.90,
      weight:      1,
    })
      .addTo(terrainLayer)
      .bindTooltip(tipHtml, { className: "terrain-tip" });
  });

  // ── Landing zones — land cells only (3 safest) ────────────────────────────
  const landCells   = cells.filter((c) => !c.is_water);
  const waterCells  = cells.filter((c) => c.is_water);
  const sortedLand  = [...landCells].sort((a, b) => a.risk - b.risk);
  const lzLabels    = ["Primary LZ", "Secondary LZ", "Emergency LZ"];

  sortedLand.slice(0, 3).forEach((cell) => {
    L.polygon(cell.corners, {
      color:       "#80ffdb",
      weight:      2,
      fillOpacity: 0.08,
      dashArray:   "6,5",
    }).addTo(lzLayer);
  });

  // ── Zones panel ────────────────────────────────────────────────────────────
  const zonesEl = document.getElementById("zones");
  zonesEl.innerHTML = "";

  if (terrain.is_water) {
    const diveWarn = document.createElement("div");
    diveWarn.className = "lz-item";
    diveWarn.style.color = "#60a5fa";
    diveWarn.innerHTML = `<strong>DITCHING AREA</strong><span>Water — no land LZ found</span>`;
    zonesEl.appendChild(diveWarn);
  } else if (!sortedLand.length) {
    const none = document.createElement("div");
    none.className = "lz-item";
    none.innerHTML = `<strong>No viable LZ</strong><span>All terrain high-risk</span>`;
    zonesEl.appendChild(none);
  } else {
    sortedLand.slice(0, 3).forEach((cell, i) => {
      const div = document.createElement("div");
      div.className = "lz-item";
      div.innerHTML = `<strong>${lzLabels[i]}</strong><span>Risk ${cell.risk}</span>`;
      zonesEl.appendChild(div);
    });
  }

  // ── Decision panel ─────────────────────────────────────────────────────────
  const isOcean     = terrain.is_water;
  const recommended = (data.options || []).find((o) => o.recommended);

  if (!selectedAcId) {
    // No aircraft selected — don't show fictitious flight-specific guidance
    document.getElementById("decisionBox").innerHTML =
      `<span style="color:#667;font-style:italic">No aircraft selected.</span><br>` +
      `<span style="color:#4a5a7a;font-size:0.85em">Select a flight from the traffic list or click a marker to begin emergency analysis.</span>`;
  } else {
    let decisionTitle, decisionReason;
    if (isOcean) {
      decisionTitle  = "DITCHING ADVISORY — Water terrain detected";
      decisionReason = `Surface: ${terrain.surface_type || "ocean"} | Elev: ${terrain.elevation_m ?? "--"} m`;
    } else if (recommended) {
      decisionTitle  = recommended.description;
      decisionReason = `Success probability: ${Math.round((recommended.success_probability || 0) * 100)}%`;
    } else {
      decisionTitle  = data.alerts?.[0]?.message || "Monitoring situation...";
      decisionReason = `Risk level: ${Math.round(failRisk * 100)}%`;
    }

    // A — Safest LZ coordinates (from lowest-risk land cell, updated every WS tick)
    let lzHtml = "";
    if (!isOcean && sortedLand.length) {
      const sc     = sortedLand[0];
      const rawLat = (sc.corners[0][0] + sc.corners[2][0]) / 2;
      const rawLon = (sc.corners[0][1] + sc.corners[2][1]) / 2;
      const latStr = `${Math.abs(rawLat).toFixed(4)}°${rawLat >= 0 ? "N" : "S"}`;
      const lonStr = `${Math.abs(rawLon).toFixed(4)}°${rawLon >= 0 ? "E" : "W"}`;
      lzHtml = `<div class="lz-item" style="margin-top:6px">` +
        `<strong>Safest LZ</strong>` +
        `<span>${latStr}, ${lonStr} &mdash; Risk ${sc.risk}</span>` +
        `</div>`;
    } else if (isOcean) {
      lzHtml = `<div class="lz-item" style="margin-top:6px">` +
        `<strong>Safest LZ</strong><span>Water &mdash; no land LZ</span></div>`;
    }

    // B — Nearest airport from Overpass (populated async after aircraft selection).
    // Distance and bearing are recalculated live from the aircraft's current position
    // every WS tick so they stay accurate as the flight moves.
    let aptHtml = "";
    if (_nearestAirport) {
      const tag = _nearestAirport.icao ? ` (${_nearestAirport.icao})` : "";
      let distBrgStr;
      if (_nearestAirport.lat != null && _nearestAirport.lon != null) {
        const liveDist = Math.round(haversine(_riskLat, _riskLon,
                                              _nearestAirport.lat, _nearestAirport.lon) * 10) / 10;
        const liveBrg  = Math.round(bearingTo(_riskLat, _riskLon,
                                              _nearestAirport.lat, _nearestAirport.lon));
        distBrgStr = `${liveDist} km · ${liveBrg}°`;
      } else {
        distBrgStr = `${_nearestAirport.dist_km} km`;
      }
      aptHtml = `<div class="lz-item">` +
        `<strong>Nearest airport</strong>` +
        `<span>${esc(_nearestAirport.name)}${esc(tag)} &mdash; ${distBrgStr}</span>` +
        `</div>`;
    } else {
      aptHtml = `<div class="lz-item">` +
        `<strong>Nearest airport</strong>` +
        `<span style="color:#4a5a7a;font-style:italic">Searching&hellip;</span>` +
        `</div>`;
    }

    document.getElementById("decisionBox").innerHTML =
      `<strong>${esc(decisionTitle)}</strong><br>` +
      `<span style="color:#99a8c6;font-size:0.85em">${esc(decisionReason)}</span>` +
      lzHtml + aptHtml;
  }

  // ── Stats ──────────────────────────────────────────────────────────────────
  const selAc = aircraftFeed.find((a) => a.id === selectedAcId);
  document.getElementById("source").textContent   = selAc ? "live traffic" : "manual";
  document.getElementById("status").textContent   = selAc ? "live traffic selected" : "area mode";
  const trueAlt = data.true_altitude_ft;
  document.getElementById("altitude").textContent =
    trueAlt != null ? `${Math.round(trueAlt)} ft (true)` : `${Math.round(currentAlt)} ft`;
  document.getElementById("speed").textContent    = `${Math.round(currentSpd)} kt`;
  document.getElementById("heading").textContent  = `${Math.round(currentHdg)}°`;
  document.getElementById("riskLevel").textContent =
    data.alerts?.[0]?.severity || "--";

  // ── Layers panel ───────────────────────────────────────────────────────────
  const layersEl = document.getElementById("layers");
  layersEl.innerHTML = "";

  // Helper: map a [0,1] score to a colour (invert=true → lower score = safer)
  function _scoreColor(score, invert = false) {
    const s = invert ? score : 1 - score;   // s=0 → safe (green), s=1 → danger (red)
    if (s < 0.25) return "#2cb64f";
    if (s < 0.50) return "#d8d62b";
    if (s < 0.75) return "#ff9c00";
    return "#ba2627";
  }

  // Helper: build a scored lz-item row with a coloured left accent bar
  function _scoredRow(label, display, score, invert = false) {
    const col = score != null ? _scoreColor(score, invert) : "#4a5a7a";
    const div = document.createElement("div");
    div.className = "lz-item";
    div.style.borderLeft  = `3px solid ${col}`;
    div.style.paddingLeft = "6px";
    div.innerHTML = `<strong>${esc(label)}</strong><span>${esc(String(display))}</span>`;
    return div;
  }

  // ── C1: Ground Safety — best available LZ safety in the current grid ─────
  const groundSafety = sortedLand.length ? Math.round((1 - sortedLand[0].risk) * 100) / 100 : null;
  layersEl.appendChild(_scoredRow(
    "Ground Safety",
    groundSafety != null ? groundSafety : "--",
    groundSafety,
    false   // higher ground safety = greener
  ));

  // ── C2: Flight State — overall aircraft risk from flight parameters ────────
  const flightState = data.risk?.overall ?? null;
  layersEl.appendChild(_scoredRow(
    "Flight State",
    flightState != null ? Math.round(flightState * 100) / 100 : "--",
    flightState != null ? flightState : null,
    true    // higher risk = redder
  ));

  // ── C3: Weather Risk — weather contribution to overall risk ───────────────
  const weatherRiskScore = data.risk?.weather_risk ?? null;
  layersEl.appendChild(_scoredRow(
    "Weather Risk",
    weatherRiskScore != null ? Math.round(weatherRiskScore * 100) / 100 : "--",
    weatherRiskScore != null ? weatherRiskScore : null,
    true
  ));

  // ── C4: Air Traffic — aircraft within 20 km of current position ───────────
  const within20 = aircraftFeed.filter((a) => a.distance_km <= 20).length;
  const atScore  = Math.min(1.0, within20 / 10);   // normalise: 10 aircraft = max
  layersEl.appendChild(_scoredRow(
    "Air Traffic",
    `${within20} within 20 km`,
    atScore,
    true    // more traffic = higher risk
  ));

  // ── C4b: Crowd Density — max OSM crowd score across the 9×9 risk grid ────
  // "crowd" is the per-cell OSM signal (amenity nodes + residential landuse).
  // crowd_ready=false means Overpass is still fetching in the background;
  // show "Fetching…" rather than a misleading 0% score.
  if (!data.crowd_ready) {
    layersEl.appendChild(_scoredRow("Crowd Density", "Fetching\u2026", null));
  } else {
    const crowdScores = (data.cells || []).map(c => c.crowd ?? 0);
    const maxCrowd    = crowdScores.length ? Math.max(...crowdScores) : null;
    const meanCrowd   = crowdScores.length ? crowdScores.reduce((a,b)=>a+b,0)/crowdScores.length : null;
    if (maxCrowd != null) {
      const crowdLabel = `max ${Math.round(maxCrowd * 100)}%  avg ${Math.round(meanCrowd * 100)}%`;
      layersEl.appendChild(_scoredRow(
        "Crowd Density",
        crowdLabel,
        maxCrowd,   // score: 0=empty→green, 1=dense→red
        true        // higher crowd = higher risk
      ));
    } else {
      layersEl.appendChild(_scoredRow("Crowd Density", "No data", null));
    }
  }

  // ── C5: Runway Option — nearest aerodrome distance (updated live) ───────────
  if (_nearestAirport) {
    const liveDist = (_nearestAirport.lat != null && _nearestAirport.lon != null)
      ? Math.round(haversine(_riskLat, _riskLon,
                             _nearestAirport.lat, _nearestAirport.lon) * 10) / 10
      : _nearestAirport.dist_km;
    const rwyScore = Math.max(0, Math.min(1, 1 - liveDist / 150));
    const tag      = _nearestAirport.icao ? ` (${_nearestAirport.icao})` : "";
    layersEl.appendChild(_scoredRow(
      "Runway Option",
      `${liveDist} km${tag}`,
      rwyScore,
      false   // closer airport = greener
    ));
  } else {
    layersEl.appendChild(_scoredRow("Runway Option", "Searching…", null));
  }

  // ── C6: Road Candidate — nearest major road ───────────────────────────────
  if (_nearestRoad) {
    const rdScore = Math.max(0, Math.min(1, 1 - _nearestRoad.dist_km / 30));
    layersEl.appendChild(_scoredRow(
      "Road Candidate",
      `${_nearestRoad.type} · ${_nearestRoad.dist_km} km`,
      rdScore,
      false   // closer road = greener
    ));
  } else {
    layersEl.appendChild(_scoredRow("Road Candidate", "Searching…", null));
  }

  // ── Divider ──────────────────────────────────────────────────────────────
  const divider = document.createElement("div");
  divider.style.cssText = "border-top:1px solid #1e2d4a;margin:4px 0;";
  layersEl.appendChild(divider);

  // ── Terrain & weather detail rows (existing) ──────────────────────────────
  const elevLabel = terrain.is_water
    ? (terrain.elevation_m != null ? `${Math.abs(Math.round(terrain.elevation_m))} m depth` : "--")
    : (terrain.elevation_m != null ? `${Math.round(terrain.elevation_m)} m` : "--");

  const layerRows = [
    ["Surface Type",   terrain.surface_type ?? "--"],
    [terrain.is_water ? "Depth" : "Elevation", elevLabel],
    ["Slope",          terrain.is_water ? "0° (water)" : (terrain.slope_deg != null ? `${terrain.slope_deg}°` : "--")],
    ["Landing",        terrain.landing_viable === false ? "Not viable" : (terrain.landing_viable ? "Viable" : "--")],
    ["Wind",           weather.wind_speed_kts != null ? `${weather.wind_speed_kts} kt / ${weather.wind_direction_deg}°` : "--"],
    ["Visibility",     weather.visibility_m  != null ? `${(weather.visibility_m / 1000).toFixed(1)} km` : "--"],
    ["Precipitation",  weather.precipitation_mm != null ? `${weather.precipitation_mm} mm` : "--"],
  ];

  layerRows.forEach(([k, v]) => {
    const div = document.createElement("div");
    div.className = "lz-item";
    div.innerHTML = `<strong>${esc(k)}</strong><span>${esc(v)}</span>`;
    layersEl.appendChild(div);
  });

  // ── Ops view ───────────────────────────────────────────────────────────────
  const g = data.guidance || {};
  const p = data.probabilistic || {};
  if (!selectedAcId) {
    document.getElementById("opsInfo").innerHTML =
      `<span style="color:#667;font-style:italic">Awaiting aircraft selection.</span><br>` +
      `<span style="color:#4a5a7a;font-size:0.85em">Flight guidance, AGL, time-to-ground and probability metrics will appear here once a flight is selected.</span>`;
  } else {
    document.getElementById("opsInfo").innerHTML =
      `${esc(g.action || "--")}<br>` +
      `AGL: ${Math.round(g.agl_ft || 0)} ft &nbsp;|&nbsp; Time to ground: ${g.time_to_ground_min ?? "--"} min<br>` +
      `Safe heading: ${g.safe_heading_deg ?? "--"}° &nbsp;|&nbsp; Rec. speed: ${g.recommended_speed_kts ?? "--"} kt<br>` +
      `Success: ${Math.round((p.success || 0) * 100)}% &nbsp;|&nbsp; Confidence: ${Math.round((p.confidence || 0) * 100)}%`;

    const vsRisk = data.vs_risk || 0;
    if (vsRisk > 0.1) {
      document.getElementById("opsInfo").innerHTML +=
        `<br><span style="color:#ba2627;font-weight:bold">&#9888; VS Risk: ${Math.round(vsRisk*100)}%</span>`;
    }
    const vsG = g.vs_guidance;
    if (vsG) {
      document.getElementById("opsInfo").innerHTML +=
        `<br><span style="color:#ff9c00;font-weight:bold">${esc(vsG)}</span>`;
    }

    const notams = data.notams || {};
    const closedArr = notams.closed || [];
    const contamArr = notams.contaminated || [];
    if (closedArr.length || contamArr.length) {
      let badge = '<br><span style="color:#ba2627;font-weight:bold">[NOTAM]</span> ';
      if (closedArr.length) badge += `Closed: ${closedArr.join(', ')} `;
      if (contamArr.length) badge += `Contaminated: ${contamArr.join(', ')}`;
      document.getElementById("opsInfo").innerHTML += badge;
    }
  }

  // ── Heading-up tracking: keep map rotated to aircraft heading ─────────────
  // Runs every WS tick (≈1.5 s) so bearing stays current as the aircraft turns.
  // Also pans the map to follow the aircraft when it has moved > 0.5 km.
  if (selectedAcId) {
    setMapBearing(currentHdg);

    // Update airplane icon position on the marker.
    // Icon is always airplaneIcon(0): Leaflet.Rotate does not CSS-rotate marker icons,
    // only position-translates them.  The map pane is already rotated to put the heading
    // at the screen top, so SVG-up = screen-up = heading direction.
    if (selectedAcMarker) {
      selectedAcMarker.setLatLng([_riskLat, _riskLon]);
      selectedAcMarker.setIcon(airplaneIcon(0));
    }

    // Pan the map to follow the aircraft if it has drifted > 0.5 km from centre
    const centre = map.getCenter();
    const drift  = haversine(centre.lat, centre.lng, _riskLat, _riskLon);
    if (drift > 0.5) {
      map.panTo([_riskLat, _riskLon], { animate: true, duration: 1.0 });
    }
  }
}

// ── WebSocket connection ───────────────────────────────────────────────────────
let wsReconnectTimer = null;
let _wsRetryDelay    = 1500;
let _voiceManuallySet = false;
let _lowRiskTickCount = 0;

function autoManageVoice() {
  if (_voiceManuallySet) return;
  if (!latestData) return;
  const lvl = latestData.risk?.level;
  if (lvl === "CRITICAL" || lvl === "HIGH") {
    _lowRiskTickCount = 0;
    if (!_voiceEnabled) {
      _voiceEnabled = true;
      const btn = document.getElementById("voiceBtn");
      btn.textContent = "Voice On";
      btn.classList.add("voice-on");
      btn.classList.remove("voice-off");
    }
  } else {
    _lowRiskTickCount++;
    if (_lowRiskTickCount > 5 && _voiceEnabled) {
      _voiceEnabled = false;
      const btn = document.getElementById("voiceBtn");
      btn.textContent = "Voice Off";
      btn.classList.add("voice-off");
      btn.classList.remove("voice-on");
    }
  }
}

function setWsStatus(state) {
  const dot   = document.getElementById("wsDot");
  const label = document.getElementById("wsLabel");
  if (!dot || !label) return;
  const cfg = {
    connected:    { color: "#2cb64f", text: "Live" },
    reconnecting: { color: "#ff9c00", text: "Reconnecting…" },
    disconnected: { color: "#ba2627", text: "Offline" },
  };
  const c = cfg[state] || cfg.disconnected;
  dot.style.background   = c.color;
  dot.style.boxShadow    = `0 0 6px ${c.color}88`;
  label.textContent      = c.text;
}

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  setWsStatus("reconnecting");
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/${sessionId}`);

  ws.onopen = () => {
    setWsStatus("connected");
    _wsRetryDelay = 1500;
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  };

  ws.onmessage = (e) => {
    setWsStatus("connected");
    draw(JSON.parse(e.data));
  };

  ws.onclose = () => {
    setWsStatus("reconnecting");
    wsReconnectTimer = setTimeout(connectWS, _wsRetryDelay);
    _wsRetryDelay = Math.min(30000, _wsRetryDelay * 1.5);
  };

  ws.onerror = () => {
    setWsStatus("reconnecting");
    // onerror is always followed by onclose, so reconnect is handled there
  };
}

// ── Location search via Nominatim ─────────────────────────────────────────────
// For aviation use, the airport is always the reference point.
// We query "{query} airport" and the plain query in parallel, then pick the
// airport result if Nominatim identifies it as an aeroway/aerodrome.
async function searchLocation(query) {
  query = (query || "").trim();
  if (!query) return;
  const notice = document.getElementById("appNotice");
  notice.textContent = `Searching "${query}"…`;

  const NOM = "https://nominatim.openstreetmap.org/search";
  const hdr = { headers: { "Accept-Language": "en", "User-Agent": "SETL-EFB/1.0" } };

  try {
    // Fire airport-specific and plain-city queries in parallel
    const [aptData, cityData] = await Promise.all([
      fetch(`${NOM}?q=${encodeURIComponent(query + " airport")}&format=json&limit=5`, hdr).then((r) => r.json()),
      fetch(`${NOM}?q=${encodeURIComponent(query)}&format=json&limit=1`, hdr).then((r) => r.json()),
    ]);

    // Prefer a result that Nominatim classifies as an aeroway/aerodrome
    const aptResult = aptData.find((r) =>
      r.class === "aeroway" ||
      r.type  === "aerodrome" ||
      (r.display_name || "").toLowerCase().includes("airport") ||
      (r.display_name || "").toLowerCase().includes("aerodrome") ||
      (r.display_name || "").toLowerCase().includes("airfield")
    );

    // Use airport coordinates when found; fall back to city centre
    const pick    = aptResult || cityData[0];
    if (!pick) { notice.textContent = `Location not found: ${query}`; return; }

    const lat     = parseFloat(pick.lat);
    const lon     = parseFloat(pick.lon);
    // Build a clean label; add "(Airport)" only if the name doesn't already say so
    const firstName = pick.display_name.split(",")[0];
    const label = aptResult && !firstName.toLowerCase().includes("airport")
                    && !firstName.toLowerCase().includes("aerodrome")
                    && !firstName.toLowerCase().includes("airfield")
                  ? firstName + " Airport"
                  : firstName;

    map.flyTo([lat, lon], 11, { animate: true, duration: 0.8 });
    currentLat = lat;
    currentLon = lon;

    // ── Immediate UI clear — don't leave stale data from the old location ──
    aircraftFeed     = [];
    _cachedOskyLocal = [];
    _oskyLastCall    = 0;
    selectedAcId     = null;
    _selPrevLat      = null;
    _selPrevLon      = null;
    _stopDR();
    _riskLat         = lat;
    _riskLon         = lon;
    // Reset Overpass context so new location gets fresh lookups
    _nearestAirport  = null;
    _nearestRoad     = null;
    _airportFetchKey = "";
    _roadFetchKey    = "";

    // Clear the aircraft list immediately so old flights vanish at once
    drawTrafficList();

    // Clear stale terrain grid cells
    terrainLayer.clearLayers();
    lzLayer.clearLayers();

    // Blank the Decision and Ops panels so stale analysis doesn't linger
    const loading = "<em style='color:#667'>Loading…</em>";
    document.getElementById("decisionBox").innerHTML = loading;
    document.getElementById("opsInfo").innerHTML     = loading;

    // Tell backend about the new location before the next WS tick.
    // Reset altitude/speed to defaults so stale aircraft values from a
    // previously selected flight do not persist into the new area-mode session.
    await fetch(`/api/live-state/${sessionId}`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({
        latitude:    lat,
        longitude:   lon,
        altitude_ft: 5000,
        speed_kts:   100,
      }),
    });

    // Fetch aircraft — auto-select nearest when results arrive
    _autoSelectFirst = true;
    fetchAircraft();

    // Fire context fetches for the new location
    fetchNearestAirport(lat, lon);
    fetchNearestRoad(lat, lon);
    notice.textContent = `Moved to: ${label}`;
  } catch (_) {
    document.getElementById("appNotice").textContent = "Search failed — check connection.";
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  // Session + OpenSky credentials load in parallel
  const [sessRes, credsRes] = await Promise.all([
    fetch("/api/session"),
    fetch("/api/opensky-creds"),
  ]);
  const data  = await sessRes.json();
  const creds = await credsRes.json();

  sessionId       = data.session_id;
  _oskyAuthHeader = creds.auth || null;   // null = anonymous fallback

  const field = document.getElementById("sessionId");
  field.value    = sessionId.slice(0, 8) + "…";
  field.readOnly = true;
  field.style.color  = "#99a8c6";
  field.style.cursor = "default";

  // Set placeholder state for panels that require an active aircraft selection
  document.getElementById("decisionBox").innerHTML =
    `<span style="color:#667;font-style:italic">No aircraft selected.</span><br>` +
    `<span style="color:#4a5a7a;font-size:0.85em">Select a flight from the traffic list or click a marker to begin emergency analysis.</span>`;
  document.getElementById("opsInfo").innerHTML =
    `<span style="color:#667;font-style:italic">Awaiting aircraft selection.</span><br>` +
    `<span style="color:#4a5a7a;font-size:0.85em">Flight guidance, AGL, time-to-ground and probability metrics will appear here once a flight is selected.</span>`;

  connectWS();
  _autoSelectFirst = true;   // auto-select nearest aircraft on first load
  fetchAircraft();
  _acInterval = setInterval(fetchAircraft, 6000);
}

// ── Aircraft poll interval — paused when tab is hidden ────────────────────────
// Storing the interval ID lets us clear it when the user switches away and
// restart it the moment they return, with an immediate sync call so the traffic
// list is never more than one tick stale after a tab switch.
let _acInterval = null;

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    clearInterval(_acInterval);
    _acInterval = null;
  } else {
    fetchAircraft();
    _acInterval = setInterval(fetchAircraft, 6000);
  }
});

// ── Event listeners ───────────────────────────────────────────────────────────
document.getElementById("connectBtn").addEventListener("click", async () => {
  if (ws) { ws.close(); ws = null; }
  const res  = await fetch("/api/session");
  const data = await res.json();
  sessionId  = data.session_id;
  document.getElementById("sessionId").value = sessionId.slice(0, 8) + "…";
  connectWS();
  clearInterval(_acInterval);
  fetchAircraft();
  _acInterval = setInterval(fetchAircraft, 6000);
  document.getElementById("appNotice").textContent = "Reconnected.";
});

const _demoBtnEl = document.getElementById("demoBtn");
if (_demoBtnEl) _demoBtnEl.addEventListener("click", async () => {
  // Demo: Delhi area, busy traffic zone
  const lat = 28.5562, lon = 77.1000;
  currentLat  = lat;  currentLon  = lon;
  currentAlt  = 2725; currentSpd  = 177; currentHdg  = 272;
  // Risk state aligns with home for demo (no aircraft pre-selected)
  _riskLat    = lat;  _riskLon    = lon;
  _selPrevLat = null; _selPrevLon = null;
  selectedAcId = null;
  _stopDR();
  _cachedOskyLocal = []; _oskyLastCall = 0;
  await fetch(`/api/live-state/${sessionId}`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ latitude: lat, longitude: lon, altitude_ft: 2725, speed_kts: 177, heading_deg: 272 }),
  });
  map.setView([lat, lon], 10);
  fetchAircraft();
  document.getElementById("appNotice").textContent = "Demo mode active — Delhi approach area.";
});

document.getElementById("locationBtn").addEventListener("click", () => {
  searchLocation(document.getElementById("locationSearch").value);
});
document.getElementById("locationSearch").addEventListener("keydown", (e) => {
  if (e.key === "Enter") searchLocation(document.getElementById("locationSearch").value);
});

document.getElementById("trafficSearch").addEventListener("input", () => drawTrafficList());
document.getElementById("trafficSearchBtn").addEventListener("click", () => drawTrafficList());

document.getElementById("nightModeBtn").addEventListener("click", toggleNightMode);

document.getElementById("voiceBtn").addEventListener("click", () => {
  _voiceManuallySet = true;
  _voiceEnabled = !_voiceEnabled;
  const btn = document.getElementById("voiceBtn");
  btn.textContent = _voiceEnabled ? "Voice On" : "Voice Off";
  btn.classList.toggle("voice-on",  _voiceEnabled);
  btn.classList.toggle("voice-off", !_voiceEnabled);
  if (_voiceEnabled) speakAlert("Voice alerts activated. SETL Emergency Flight Bag ready.");
  document.getElementById("appNotice").textContent =
    _voiceEnabled ? "Voice alerts ON." : "Voice alerts OFF.";
});

document.getElementById("analyticsBtn").addEventListener("click", showAnalytics);
document.getElementById("closeAnalyticsBtn").addEventListener("click", () => {
  document.getElementById("analyticsModal").style.display = "none";
});

document.getElementById("clearTrackBtn").addEventListener("click", async () => {
  selectedAcId = null;
  _selPrevLat  = null;
  _selPrevLon  = null;
  _stopDR();
  _riskLat     = currentLat;   // restore risk grid to home base
  _riskLon     = currentLon;
  if (selectedAcMarker) { map.removeLayer(selectedAcMarker); selectedAcMarker = null; }

  // Restore north-up map orientation
  setMapBearing(0);

  // Re-centre the risk grid on home; switch back to area (centred, north-aligned) grid.
  // Also reset altitude/speed to defaults so stale aircraft values do not linger.
  await fetch(`/api/live-state/${sessionId}`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({
      latitude:     currentLat,
      longitude:    currentLon,
      altitude_ft:  5000,
      speed_kts:    100,
      heading_deg:  0,
      forward_grid: false,
    }),
  });
  // Immediately show no-selection placeholders — don't wait for next WS tick
  document.getElementById("decisionBox").innerHTML =
    `<span style="color:#667;font-style:italic">No aircraft selected.</span><br>` +
    `<span style="color:#4a5a7a;font-size:0.85em">Select a flight from the traffic list or click a marker to begin emergency analysis.</span>`;
  document.getElementById("opsInfo").innerHTML =
    `<span style="color:#667;font-style:italic">Awaiting aircraft selection.</span><br>` +
    `<span style="color:#4a5a7a;font-size:0.85em">Flight guidance, AGL, time-to-ground and probability metrics will appear here once a flight is selected.</span>`;
  drawTraffic();
  drawTrafficList();
  document.getElementById("appNotice").textContent = "Returned to area mode.";
});

// ── Voice Alert Engine (Web Speech API — zero dependencies) ──────────────
function speakAlert(txt) {
  if (!_voiceEnabled || !window.speechSynthesis) return;
  const now = Date.now();
  if (txt === _lastVoiceTxt && now - _lastVoiceTs < 14000) return;
  _lastVoiceTxt = txt; _lastVoiceTs = now;
  try {
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(txt);
    u.rate = 0.90; u.pitch = 1.0; u.volume = 1.0;
    window.speechSynthesis.speak(u);
  } catch(e) {
    _voiceEnabled = false;
    console.warn("Voice alert failed, disabling:", e);
  }
}

function buildVoiceText(data) {
  if (!data) return null;
  const lvl     = data.risk?.level;
  const opts    = data.options || [];
  const primary = opts.find(o => o.type === "PRIMARY") || opts[0];
  const reach   = data.reachability || {};
  const sigs    = data.sigmets || [];
  if (lvl === "CRITICAL") {
    let msg = "Warning. Critical risk. ";
    if (reach.green_reachable === 0) msg += "No safe zone within glide range. ";
    if (sigs.length) msg += `SIGMET active: ${sigs[0].hazard}. `;
    if (primary?.bearing_deg != null)
      msg += `Best option bearing ${primary.bearing_deg} degrees`;
    if (primary?.distance_nm)
      msg += `, ${primary.distance_nm} nautical miles`;
    msg += `. Success ${Math.round((primary?.success_probability||0)*100)} percent.`;
    return msg;
  }
  if (lvl === "HIGH") {
    let msg = "High risk. ";
    if (sigs.length) msg += `SIGMET ${sigs[0].hazard} active. `;
    msg += `Prepare for emergency landing. Heading ${data.guidance?.safe_heading_deg||"--"} degrees.`;
    return msg;
  }
  return null;
}

function toggleNightMode() {
  _nightMode = !_nightMode;
  document.body.classList.toggle("night-mode", _nightMode);
  document.getElementById("nightModeBtn").textContent =
    _nightMode ? "Day Mode" : "Night Mode";
}

function updateGlideOverlay(data) {
  const el = document.getElementById("glideOverlay");
  const tx = document.getElementById("glideText");
  if (!el || !data?.glide_range_nm) { if(el) el.style.display="none"; return; }
  const r = data.reachability || {};
  el.style.display = "block";
  tx.textContent   =
    `Glide ${data.glide_range_nm} nm  |  ` +
    `Reachable ${r.reachable_cells??'--'}/81  |  ` +
    `Safe reachable ${r.green_reachable??'--'}`;
}

async function showAnalytics() {
  document.getElementById("analyticsModal").style.display = "flex";
  document.getElementById("analyticsContent").innerHTML =
    "<p style='color:#99a8c6;padding:12px'>Loading analytics...</p>";
  try {
    const resp = await fetch("/api/analytics");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const json = await resp.json();
    const a    = json.analytics || {};
    const anoms= json.anomalies || [];
    let h = "";
    const row = (label, val, col) =>
      `<div class="analytics-stat"><span>${label}</span>` +
      `<span${col?` style="color:${col}"`:""}>${val}</span></div>`;
    h += `<div class="analytics-section-title">Session Overview</div>`;
    h += row("Total log records",   a.total_records||0);
    h += row("Unique sessions",     a.unique_sessions||0);
    h += row("Critical events",     a.critical_events||0,  "#ba2627");
    h += row("High risk events",    a.high_events||0,       "#ff9c00");
    h += `<div class="analytics-section-title">Decision Quality</div>`;
    h += row("Mean success probability", ((a.mean_success_prob||0)*100).toFixed(1)+"%");
    h += row("Mean green cells/tick",    a.mean_green_cells||0);
    h += `<div class="analytics-section-title">System Performance</div>`;
    h += row("Tick latency p50", (a.tick_ms_p50||0)+" ms");
    h += row("Tick latency p95", (a.tick_ms_p95||0)+" ms");
    h += row("Terrain live %",   (a.pct_terrain_live||0)+"%");
    h += row("Real METAR %",     (a.pct_metar||0)+"%");
    h += `<div class="analytics-section-title">Risk Distribution</div>`;
    const colors = {CRITICAL:"#ba2627",HIGH:"#ff9c00",MODERATE:"#d8d62b",LOW:"#2cb64f"};
    Object.entries(a.risk_distribution||{}).forEach(([k,v]) => h += row(k,v,colors[k]));
    h += `<div class="analytics-section-title">Top Aircraft Types</div>`;
    Object.entries(a.top_aircraft_types||{}).forEach(([k,v]) => h += row(k,v));
    h += `<div class="analytics-section-title">Weather Sources</div>`;
    Object.entries(a.wx_source_breakdown||{}).forEach(([k,v]) => h += row(k,v));
    if (anoms.length) {
      h += `<div class="analytics-section-title" style="color:#ff9c00">Anomalies</div>`;
      anoms.forEach(a => { h += `<div style="color:#ff9c00;padding:4px 0">${a}</div>`; });
    }
    document.getElementById("analyticsContent").innerHTML = h;
  } catch(e) {
    document.getElementById("analyticsContent").innerHTML =
      `<p style='color:#ba2627'>Error: ${e.message}</p>`;
  }
}

function updateSigmetBanner(data) {
  let el = document.getElementById("sigmetBanner");
  if (!el) return;
  const sigs = data?.sigmets || [];
  if (!sigs.length) { el.style.display="none"; return; }
  el.style.display = "block";
  el.innerHTML = "SIGMET: " +
    sigs.slice(0,2).map(s=>`${s.hazard} FL${s.alt_lo_ft/100|0}-FL${s.alt_hi_ft/100|0}`).join(" | ");
}

init();
