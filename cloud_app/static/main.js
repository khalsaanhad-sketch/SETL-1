// ── Map setup: Esri satellite basemap + labels overlay ───────────────────────
const map = L.map("map", { maxZoom: 19 }).setView([28.6139, 77.2090], 10);

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
let _oskyAuthHeader       = null;   // set from /api/opensky-creds on init
let _cachedOskyLocal      = [];     // local OpenSky results, persist between fetchAircraft() calls

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

// ── Risk grid (computed client-side from backend risk score) ─────────────────
function computeGrid(lat, lon, baseRisk, gridSize = 8, cellDeg = 0.004) {
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

function drawTraffic() {
  trafficLayer.clearLayers();
  if (selectedAcMarker) { map.removeLayer(selectedAcMarker); selectedAcMarker = null; }

  aircraftFeed.forEach((ac) => {
    const isSel = ac.id === selectedAcId;
    const m = L.marker([ac.latitude, ac.longitude], {
      icon:            trafficIcon(isSel),
      zIndexOffset:    isSel ? 2000 : 0,
      bubblingMouseEvents: false,
    }).addTo(trafficLayer);

    m.bindTooltip(
      `<strong>${esc(ac.callsign)}</strong><br>` +
      `Alt ${Math.round(ac.altitude_ft)} ft<br>` +
      `Spd ${Math.round(ac.speed_kts)} kt<br>` +
      `Hdg ${Math.round(ac.heading_deg)}°<br>` +
      `Dist ${ac.distance_km} km`,
      { direction: "top", offset: [0, -4] }
    );
    m.on("click", (e) => { L.DomEvent.stopPropagation(e); selectAircraft(ac); });
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
      `<span>${Math.round(ac.altitude_ft)} ft • ${Math.round(ac.speed_kts)} kt • ${ac.distance_km} km</span>`;
    row.addEventListener("click", () => selectAircraft(ac));
    list.appendChild(row);
  });
}

// ── Select aircraft → update backend state ────────────────────────────────────
async function selectAircraft(ac) {
  selectedAcId = ac.id;
  currentLat   = ac.latitude;
  currentLon   = ac.longitude;
  currentAlt   = ac.altitude_ft;
  currentSpd   = ac.speed_kts;
  currentHdg   = ac.heading_deg;

  await fetch(`/api/live-state/${sessionId}`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({
      latitude:    ac.latitude,
      longitude:   ac.longitude,
      altitude_ft: ac.altitude_ft,
      speed_kts:   ac.speed_kts,
      heading_deg: ac.heading_deg,
    }),
  });

  // Zoom 10: shows ~80 km radius — risk grid readable + city/terrain context
  map.flyTo([ac.latitude, ac.longitude], 10, { animate: true, duration: 0.8 });
  drawTraffic();
  drawTrafficList();
  if (latestData) draw(latestData);
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

// ── Fetch aircraft: backend proxy + browser-direct OpenSky ────────────────────
async function fetchAircraft() {
  try {
    // Run ADS-B backend call and browser-direct OpenSky in parallel
    const [backendRes, oskyFresh] = await Promise.all([
      fetch(`/api/aircraft?lat=${currentLat}&lon=${currentLon}&radius=200`).then((r) => r.json()),
      fetchOpenSkyDirect(),
    ]);

    // ── Normalise ADS-B feed ──
    const adsbFeed = (backendRes.ac || [])
      .filter((ac) => ac.lat && ac.lon)
      .map((ac) => ({
        id:          ac.hex || "",
        callsign:    (ac.flight || "").trim() || ac.hex || "",
        icao24:      ac.hex || "",
        latitude:    ac.lat,
        longitude:   ac.lon,
        altitude_ft: ac.alt_baro || 0,
        speed_kts:   ac.gs || 0,
        heading_deg: ac.track || 0,
        distance_km: haversine(currentLat, currentLon, ac.lat, ac.lon),
        source:      "adsb",
      }));

    // ── Update local OpenSky cache when fresh data arrived ──
    if (oskyFresh.length > 0) _cachedOskyLocal = oskyFresh;

    // ── Merge: cached OpenSky base, ADS-B overwrites duplicates (higher fidelity) ──
    const merged = new Map(_cachedOskyLocal.map((a) => [a.id, a]));
    for (const ac of adsbFeed) merged.set(ac.id, ac);

    // ── Recompute distance from current location (handles stale cache + location change) ──
    aircraftFeed = [...merged.values()]
      .map((ac) => ({
        ...ac,
        distance_km: haversine(currentLat, currentLon, ac.latitude, ac.longitude),
      }))
      .sort((a, b) => a.distance_km - b.distance_km);

    // ── Track selected aircraft position → keep risk grid live as it moves ──
    if (selectedAcId) {
      const selAc = aircraftFeed.find((a) => a.id === selectedAcId);
      if (selAc) {
        // Only re-post if the aircraft has meaningfully moved (>0.1 km)
        const drift = haversine(currentLat, currentLon, selAc.latitude, selAc.longitude);
        if (drift > 0.1) {
          currentLat = selAc.latitude;
          currentLon = selAc.longitude;
          currentAlt = selAc.altitude_ft;
          currentSpd = selAc.speed_kts;
          currentHdg = selAc.heading_deg;
          // Post new position — backend immediately recalculates terrain risk grid
          fetch(`/api/live-state/${sessionId}`, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              latitude:    selAc.latitude,
              longitude:   selAc.longitude,
              altitude_ft: selAc.altitude_ft,
              speed_kts:   selAc.speed_kts,
              heading_deg: selAc.heading_deg,
            }),
          });
        }
      }
    }

    drawTraffic();
    drawTrafficList();
  } catch (_) {}
}

// ── Main draw — called on every WebSocket message ─────────────────────────────
function draw(data) {
  latestData = data;

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

  document.getElementById("decisionBox").innerHTML =
    `<strong>${esc(decisionTitle)}</strong><br>${esc(decisionReason)}`;

  // ── Stats ──────────────────────────────────────────────────────────────────
  const selAc = aircraftFeed.find((a) => a.id === selectedAcId);
  document.getElementById("source").textContent   = selAc ? "live traffic" : "manual";
  document.getElementById("status").textContent   = selAc ? "live traffic selected" : "area mode";
  document.getElementById("altitude").textContent = `${Math.round(currentAlt)} ft`;
  document.getElementById("speed").textContent    = `${Math.round(currentSpd)} kt`;
  document.getElementById("heading").textContent  = `${Math.round(currentHdg)}°`;
  document.getElementById("riskLevel").textContent =
    data.alerts?.[0]?.severity || "--";

  // ── Layers panel ───────────────────────────────────────────────────────────
  const layersEl = document.getElementById("layers");
  layersEl.innerHTML = "";

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
  document.getElementById("opsInfo").innerHTML =
    `${esc(g.action || "--")}<br>` +
    `AGL: ${Math.round(g.agl_ft || 0)} ft &nbsp;|&nbsp; Time to ground: ${g.time_to_ground_min ?? "--"} min<br>` +
    `Safe heading: ${g.safe_heading_deg ?? "--"}° &nbsp;|&nbsp; Rec. speed: ${g.recommended_speed_kts ?? "--"} kt<br>` +
    `Success: ${Math.round((p.success || 0) * 100)}% &nbsp;|&nbsp; Confidence: ${Math.round((p.confidence || 0) * 100)}%`;
}

// ── WebSocket connection ───────────────────────────────────────────────────────
let wsReconnectTimer = null;

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
    if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  };

  ws.onmessage = (e) => {
    setWsStatus("connected");
    draw(JSON.parse(e.data));
  };

  ws.onclose = () => {
    setWsStatus("reconnecting");
    wsReconnectTimer = setTimeout(connectWS, 2000);
  };

  ws.onerror = () => {
    setWsStatus("reconnecting");
  };
}

// ── Location search via Nominatim ─────────────────────────────────────────────
async function searchLocation(query) {
  query = (query || "").trim();
  if (!query) return;
  const notice = document.getElementById("appNotice");
  notice.textContent = `Searching "${query}"…`;
  try {
    const res  = await fetch(
      `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&limit=1`,
      { headers: { "Accept-Language": "en", "User-Agent": "SETL-EFB/1.0" } }
    );
    const data = await res.json();
    if (!data.length) { notice.textContent = `Location not found: ${query}`; return; }

    const lat = parseFloat(data[0].lat);
    const lon = parseFloat(data[0].lon);
    map.flyTo([lat, lon], 10, { animate: true, duration: 0.8 });
    currentLat = lat;
    currentLon = lon;

    // Clear stale aircraft cache from previous location; force immediate OpenSky refresh
    _cachedOskyLocal = [];
    _oskyLastCall    = 0;
    selectedAcId     = null;   // deselect any previously selected aircraft

    await fetch(`/api/live-state/${sessionId}`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ latitude: lat, longitude: lon }),
    });

    fetchAircraft();
    notice.textContent = `Moved to: ${data[0].display_name.split(",")[0]}`;
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

  connectWS();
  fetchAircraft();
  setInterval(fetchAircraft, 6000);
}

// ── Event listeners ───────────────────────────────────────────────────────────
document.getElementById("connectBtn").addEventListener("click", async () => {
  if (ws) { ws.close(); ws = null; }
  const res  = await fetch("/api/session");
  const data = await res.json();
  sessionId  = data.session_id;
  document.getElementById("sessionId").value = sessionId.slice(0, 8) + "…";
  connectWS();
  fetchAircraft();
  document.getElementById("appNotice").textContent = "Reconnected.";
});

document.getElementById("demoBtn").addEventListener("click", async () => {
  // Demo: Delhi area, busy traffic zone
  const lat = 28.5562, lon = 77.1000;
  currentLat = lat; currentLon = lon;
  currentAlt = 2725; currentSpd = 177; currentHdg = 272;
  await fetch(`/api/live-state/${sessionId}`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ latitude: lat, longitude: lon, altitude_ft: 2725, speed_kts: 177, heading_deg: 272 }),
  });
  map.setView([lat, lon], 12);
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

document.getElementById("clearTrackBtn").addEventListener("click", () => {
  selectedAcId = null;
  if (selectedAcMarker) { map.removeLayer(selectedAcMarker); selectedAcMarker = null; }
  drawTraffic();
  drawTrafficList();
  document.getElementById("appNotice").textContent = "Returned to area mode.";
});

init();
